"""FastAPI backend. The browser UI in flashcard/web talks to these endpoints."""

from __future__ import annotations

import datetime as dt
import os
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import cloze, commands, history
from .generator import ClaudeGenerator, GeneratorError, guess_type
from .models import FORWARD, REVERSE, Card, utcnow
from . import scheduler as scheduler_module
from .scheduler import Scheduler, build_queue, humanise, projection
from .store import DeckError, Store

WEB_DIR = Path(__file__).parent / "web"
LOCK = threading.RLock()


class CommandIn(BaseModel):
    text: str


class AnswerIn(BaseModel):
    id: str
    grade: int
    direction: str = FORWARD


class ImportIn(BaseModel):
    yaml: str
    mode: str = "merge"


class SettingsIn(BaseModel):
    values: Dict[str, Any]


def create_app(deck_path: os.PathLike | str) -> FastAPI:
    store = Store(deck_path)
    log = history.ReviewLog(store.path.parent / "reviews.csv")
    app = FastAPI(title="Deutsch B1 Karteikarten", docs_url=None, redoc_url=None)
    app.state.store = store

    def gen() -> ClaudeGenerator:
        return ClaudeGenerator()

    def sched() -> Scheduler:
        return Scheduler(store.settings)

    def fresh() -> Store:
        with LOCK:
            store.reload_if_changed()
        return store

    # ------------------------------------------------------------------ views

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(WEB_DIR / "index.html")

    app.mount("/static", StaticFiles(directory=str(WEB_DIR)), name="static")

    # ------------------------------------------------------------------ state

    def state_payload() -> Dict:
        s = fresh()
        now = utcnow()
        queue = build_queue(s.cards, s.settings, now, s.introduced_today(now))
        counts = {"due": 0, "learning": 0, "new": 0, "reverse": 0}
        for card, direction in queue:
            srs = card.srs_for(direction)
            if direction == REVERSE:
                counts["reverse"] += 1
            if srs.state == "new":
                counts["new"] += 1
            elif srs.state in ("learning", "relearning"):
                counts["learning"] += 1
            else:
                counts["due"] += 1
        return {
            "settings": s.settings,
            "counts": counts,
            "queue_size": len(queue),
            "projection": projection(s.cards, s.settings, now),
            "generator_available": gen().available,
            "deck_path": str(s.path),
            "next_due": _next_due_label(s, now),
        }

    @app.get("/api/state")
    def get_state() -> Dict:
        return state_payload()

    @app.get("/api/cards")
    def get_cards(q: str = "", type: str = "", sort: str = "due") -> Dict:
        s = fresh()
        cards = s.search(q) if q else list(s.cards)
        if type:
            cards = [c for c in cards if c.type == type]
        if sort == "alpha":
            cards.sort(key=lambda c: c.lemma.lower())
        elif sort == "created":
            cards.sort(key=lambda c: c.created or utcnow(), reverse=True)
        else:
            cards.sort(key=lambda c: (c.srs.due or utcnow()))
        if q:
            # the word you searched for belongs above cards that merely mention
            # it in a definition or an example sentence
            needle = q.strip().lower()
            def rank(card: Card) -> int:
                lemma = card.lemma.lower()
                if lemma == needle:
                    return 0
                if lemma.startswith(needle):
                    return 1
                if needle in lemma:
                    return 2
                return 3
            cards.sort(key=rank)
        return {"cards": [c.to_api() for c in cards], "total": len(s.cards)}

    def item_payload(card: Card, direction: str, scheduler: Scheduler,
                     now: dt.datetime) -> Dict:
        """One queue entry: the card, which way round it is asked, and its buttons."""
        data = card.to_api()
        data["direction"] = direction
        data["preview"] = scheduler.preview(card, now, direction)
        data["srs_active"] = card.srs_for(direction).to_dict()
        if direction == REVERSE:
            # the sentence must not contain the word you are asked to produce
            data["example_masked"] = cloze.mask(card)
            data["examples_masked"] = cloze.mask_all(card, card.examples)
        return data

    @app.get("/api/queue")
    def get_queue(limit: int = 50) -> Dict:
        s = fresh()
        now = utcnow()
        queue = build_queue(s.cards, s.settings, now, s.introduced_today(now))[:limit]
        scheduler = sched()
        return {
            "cards": [item_payload(card, direction, scheduler, now)
                      for card, direction in queue],
            "remaining": len(queue),
        }

    @app.post("/api/answer")
    def answer(payload: AnswerIn) -> Dict:
        with LOCK:
            s = fresh()
            card = s.by_id(payload.id)
            if not card:
                raise HTTPException(404, f"Karte {payload.id!r} nicht gefunden")
            direction = REVERSE if payload.direction == REVERSE else FORWARD
            srs = card.srs_for(direction)
            state_before, interval_before = srs.state, srs.interval_days
            was_new = state_before == "new"
            sched().answer(card, payload.grade, direction=direction)
            if was_new:
                s.note_introduced()
            s.save()
            try:
                log.append(card, direction, payload.grade, state_before, interval_before)
            except OSError:
                pass          # a full disk must not cost you the review itself
            return {"card": card.to_api(), "state": state_payload()}

    # ------------------------------------------------------------------ cards

    @app.post("/api/cards")
    def create_card(payload: Dict) -> Dict:
        with LOCK:
            s = fresh()
            card = Card.from_dict({**payload, "source": payload.get("source") or "manual"})
            if not card.lemma:
                raise HTTPException(400, "Ein Stichwort (lemma) wird gebraucht")
            existing = s.find_duplicate(card.lemma)
            if existing and not payload.get("allow_duplicate"):
                raise HTTPException(409, f"{existing.headword} gibt es schon "
                                         f"(id {existing.id})")
            s.add(card)
            return {"card": card.to_api(), "similar": [c.to_api() for c in s.similar(card.lemma, card.id)]}

    @app.patch("/api/cards/{card_id}")
    def patch_card(card_id: str, payload: Dict) -> Dict:
        with LOCK:
            s = fresh()
            if payload.get("lemma"):
                clash = s.find_duplicate(payload["lemma"], ignore_id=card_id)
                if clash:
                    raise HTTPException(409, f"{clash.headword} gibt es schon "
                                             f"(id {clash.id})")
            try:
                card = s.update(card_id, payload)
            except DeckError as exc:
                raise HTTPException(404, str(exc)) from exc
            return {"card": card.to_api()}

    @app.delete("/api/cards/{card_id}")
    def delete_card(card_id: str) -> Dict:
        with LOCK:
            s = fresh()
            if not s.delete(card_id):
                raise HTTPException(404, f"Karte {card_id!r} nicht gefunden")
            return {"ok": True}

    @app.post("/api/cards/{card_id}/reset")
    def reset_card(card_id: str, direction: str = "both") -> Dict:
        with LOCK:
            s = fresh()
            card = s.by_id(card_id)
            if not card:
                raise HTTPException(404, "Karte nicht gefunden")
            from .models import SRS
            if direction in ("both", FORWARD):
                card.srs = SRS()
            if direction in ("both", REVERSE):
                card.srs_reverse = SRS()
            s.save()
            return {"card": card.to_api()}

    @app.post("/api/cards/{card_id}/sentence")
    def new_sentence(card_id: str) -> Dict:
        s = fresh()
        card = s.by_id(card_id)
        if not card:
            raise HTTPException(404, "Karte nicht gefunden")
        summary = {
            "type": card.type, "lemma": card.lemma, "article": card.article,
            "definition": card.definition,
        }
        is_grammar = card.type == "grammar"
        try:
            if is_grammar:
                replacement = gen().generate_examples(
                    summary, s.known_lemmas(), s.grammar_topics(),
                    avoid=" | ".join(card.examples or [card.example]),
                )
            else:
                replacement = gen().generate_sentence(
                    summary, s.known_lemmas(), s.grammar_topics(), avoid=card.example
                )
        except GeneratorError as exc:
            raise HTTPException(502, str(exc)) from exc
        with LOCK:
            s.reload_if_changed()
            card = s.by_id(card_id)
            if is_grammar:
                card.examples = replacement
            else:
                card.example = replacement
            card.modified = utcnow()
            s.save()
            return {"card": card.to_api()}

    # ---------------------------------------------------------------- command

    @app.post("/api/command")
    def run_command(payload: CommandIn) -> JSONResponse:
        action = commands.parse(payload.text)
        kind = action.get("action")
        s = fresh()

        if kind in ("noop", "help", "review", "browse", "stats", "export"):
            return JSONResponse({**action, "state": state_payload()})

        if kind == "error":
            return JSONResponse({**action, "state": state_payload()}, status_code=200)

        if kind == "find":
            hits = s.search(action.get("query", ""))
            return JSONResponse({
                "action": "find", "query": action.get("query", ""),
                "cards": [c.to_api() for c in hits], "state": state_payload(),
            })

        if kind in ("edit", "delete", "sentence"):
            card = s.resolve(action.get("key", ""))
            if not card:
                return JSONResponse({
                    "action": "error",
                    "message": f"Keine Karte gefunden für {action.get('key','')!r}",
                    "state": state_payload(),
                })
            if kind == "edit":
                return JSONResponse({"action": "edit", "card": card.to_api(),
                                     "state": state_payload()})
            if kind == "delete":
                with LOCK:
                    s.delete(card.id)
                return JSONResponse({"action": "deleted", "id": card.id,
                                     "message": f"{card.headword} gelöscht",
                                     "state": state_payload()})
            result = new_sentence(card.id)
            return JSONResponse({"action": "updated", "card": result["card"],
                                 "message": f"Neuer Satz für {card.headword}",
                                 "state": state_payload()})

        if kind == "settings":
            with LOCK:
                s.set_settings(action["values"])
            return JSONResponse({"action": "settings", "message": "Einstellung gespeichert",
                                 "state": state_payload()})

        if kind == "add":
            return JSONResponse(_add_card(s, gen(), action["query"], action.get("hint_type", "")))

        return JSONResponse({"action": "error", "message": "Unbekannte Aktion",
                             "state": state_payload()})

    def _duplicate(existing: Card, typed: str = "") -> Dict:
        note = f" (du hast {typed!r} eingegeben)" if typed and \
            typed.strip().lower() != existing.lemma.lower() else ""
        return {"action": "duplicate", "card": existing.to_api(),
                "message": f"{existing.headword} gibt es schon{note}.",
                "state": state_payload()}

    def _add_card(s: Store, generator: ClaudeGenerator, query: str, hint: str) -> Dict:
        # first pass: the word as typed, allowing for article and capitalisation
        existing = s.find_duplicate(query)
        if existing:
            return _duplicate(existing)

        warning = ""
        try:
            data = generator.generate_card(query, s.known_lemmas(), s.grammar_topics(), hint)
            data["source"] = "claude"
        except GeneratorError as exc:
            warning = str(exc)
            data = {"lemma": query.strip(), "type": hint or guess_type(query),
                    "source": "manual", "definition": "", "example": ""}

        with LOCK:
            s.reload_if_changed()
            card = Card.from_dict(data)
            # second pass: an inflected input ("läuft", "gelaufen", "die Katze")
            # only reveals its lemma after generation, so check again here
            existing = s.find_duplicate(card.lemma)
            if existing:
                return _duplicate(existing, query)
            similar = s.similar(card.lemma)
            s.add(card)
        return {"action": "added", "card": card.to_api(), "warning": warning,
                "similar": [c.to_api() for c in similar],
                "message": f"{card.headword} angelegt", "state": state_payload()}

    # --------------------------------------------------------- import/export

    @app.get("/api/history")
    def get_history(days: int = 30, forecast_days: int = 14) -> Dict:
        s = fresh()
        days = max(7, min(int(days), 120))
        forecast_days = max(7, min(int(forecast_days), 60))
        now = utcnow()
        today = now.astimezone().date()
        since = today - dt.timedelta(days=days - 1)
        rows = log.rows(since=since)
        daily = history.activity(rows, days, today, (s.stats or {}).get("introduced"))
        return {
            "days": days,
            "activity": daily,
            "retention": history.retention(daily),
            "learned": history.learned_curve(rows, s.cards, days, today, s.settings),
            "forecast": history.forecast(s.cards, s.settings, forecast_days, now),
            "intervals": history.intervals(s.cards, s.settings),
            "summary": history.summary(rows, daily),
            "maturity_days": round(
                scheduler_module.effective_threshold(
                    scheduler_module.MATURE_INTERVAL_DAYS, s.settings, now), 2),
            "target_date": s.settings.get("target_date"),
            "log_path": str(log.path),
        }

    @app.get("/api/duplicates")
    def duplicates() -> Dict:
        s = fresh()
        return find_duplicates(s)

    @app.get("/api/export")
    def export_deck() -> PlainTextResponse:
        s = fresh()
        stamp = dt.date.today().isoformat()
        return PlainTextResponse(
            s.export_text(), media_type="application/x-yaml",
            headers={"content-disposition": f'attachment; filename="deck-{stamp}.yaml"'},
        )

    @app.post("/api/import")
    def import_deck(payload: ImportIn) -> Dict:
        with LOCK:
            s = fresh()
            try:
                result = s.import_cards(payload.yaml, payload.mode)
            except DeckError as exc:
                raise HTTPException(400, str(exc)) from exc
            return {**result, "state": state_payload()}

    @app.post("/api/settings")
    def put_settings(payload: SettingsIn) -> Dict:
        with LOCK:
            s = fresh()
            s.set_settings(payload.values)
            return state_payload()

    return app


def find_duplicates(store: Store) -> Dict:
    """Group cards that are the same word, and pairs that merely look alike."""
    from .store import SIMILAR_THRESHOLD, normalise_lemma, similarity

    groups: Dict[str, List[Card]] = {}
    for card in store.cards:
        groups.setdefault(normalise_lemma(card.lemma), []).append(card)
    exact = [[c.to_api() for c in group] for group in groups.values() if len(group) > 1]

    keys = sorted(groups)
    near = []
    for i, a in enumerate(keys):
        for b in keys[i + 1:]:
            score = similarity(a, b)
            if score >= SIMILAR_THRESHOLD:
                near.append({"score": round(score, 2),
                             "cards": [groups[a][0].to_api(), groups[b][0].to_api()]})
    near.sort(key=lambda pair: -pair["score"])
    return {"exact": exact, "near": near,
            "total": len(store.cards), "unique": len(groups)}


def _next_due_label(store: Store, now: dt.datetime) -> Optional[str]:
    future: List[dt.datetime] = [
        c.srs.due for c in store.cards if c.srs.due and c.srs.due > now
    ]
    if not future:
        return None
    return humanise((min(future) - now).total_seconds())
