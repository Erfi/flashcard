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

from . import commands
from .generator import ClaudeGenerator, GeneratorError, guess_type
from .models import Card, utcnow
from .scheduler import Scheduler, build_queue, humanise, projection
from .store import DeckError, Store

WEB_DIR = Path(__file__).parent / "web"
LOCK = threading.RLock()


class CommandIn(BaseModel):
    text: str


class AnswerIn(BaseModel):
    id: str
    grade: int


class ImportIn(BaseModel):
    yaml: str
    mode: str = "merge"


class SettingsIn(BaseModel):
    values: Dict[str, Any]


def create_app(deck_path: os.PathLike | str) -> FastAPI:
    store = Store(deck_path)
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
        counts = {"due": 0, "learning": 0, "new": 0}
        for card in queue:
            if card.srs.state == "new":
                counts["new"] += 1
            elif card.srs.state in ("learning", "relearning"):
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
        return {"cards": [c.to_api() for c in cards], "total": len(s.cards)}

    @app.get("/api/queue")
    def get_queue(limit: int = 50) -> Dict:
        s = fresh()
        now = utcnow()
        queue = build_queue(s.cards, s.settings, now, s.introduced_today(now))[:limit]
        scheduler = sched()
        return {
            "cards": [
                {**c.to_api(), "preview": scheduler.preview(c, now)} for c in queue
            ],
            "remaining": len(queue),
        }

    @app.post("/api/answer")
    def answer(payload: AnswerIn) -> Dict:
        with LOCK:
            s = fresh()
            card = s.by_id(payload.id)
            if not card:
                raise HTTPException(404, f"Karte {payload.id!r} nicht gefunden")
            was_new = card.srs.state == "new"
            sched().answer(card, payload.grade)
            if was_new:
                s.note_introduced()
            s.save()
            return {"card": card.to_api(), "state": state_payload()}

    # ------------------------------------------------------------------ cards

    @app.post("/api/cards")
    def create_card(payload: Dict) -> Dict:
        with LOCK:
            s = fresh()
            card = Card.from_dict({**payload, "source": payload.get("source") or "manual"})
            if not card.lemma:
                raise HTTPException(400, "Ein Stichwort (lemma) wird gebraucht")
            s.add(card)
            return {"card": card.to_api()}

    @app.patch("/api/cards/{card_id}")
    def patch_card(card_id: str, payload: Dict) -> Dict:
        with LOCK:
            s = fresh()
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
    def reset_card(card_id: str) -> Dict:
        with LOCK:
            s = fresh()
            card = s.by_id(card_id)
            if not card:
                raise HTTPException(404, "Karte nicht gefunden")
            from .models import SRS
            card.srs = SRS()
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
        try:
            sentence = gen().generate_sentence(
                summary, s.known_lemmas(), s.grammar_topics(), avoid=card.example
            )
        except GeneratorError as exc:
            raise HTTPException(502, str(exc)) from exc
        with LOCK:
            s.reload_if_changed()
            card = s.by_id(card_id)
            card.example = sentence
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

    def _add_card(s: Store, generator: ClaudeGenerator, query: str, hint: str) -> Dict:
        existing = s.by_lemma(query)
        if existing:
            return {"action": "duplicate", "card": existing.to_api(),
                    "message": f"{existing.headword} gibt es schon.", "state": state_payload()}

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
            s.add(card)
        return {"action": "added", "card": card.to_api(), "warning": warning,
                "message": f"{card.headword} angelegt", "state": state_payload()}

    # --------------------------------------------------------- import/export

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


def _next_due_label(store: Store, now: dt.datetime) -> Optional[str]:
    future: List[dt.datetime] = [
        c.srs.due for c in store.cards if c.srs.due and c.srs.due > now
    ]
    if not future:
        return None
    return humanise((min(future) - now).total_seconds())
