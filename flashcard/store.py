"""YAML-backed deck storage.

Design notes
------------
* One file, `data/deck.yaml`, holding settings + every card. Small decks
  (thousands of cards) load in milliseconds, so we simply re-read the file
  whenever its mtime changes. That means you can hand-edit the YAML while the
  server is running and the app picks it up.
* Writes are atomic (temp file + os.replace) and keep a `.bak` of the previous
  version, so a crash mid-write cannot leave you with a truncated deck.
"""

from __future__ import annotations

import datetime as dt
import os
import shutil
import tempfile
from pathlib import Path
from typing import Dict, Iterable, List, Optional

import yaml

from .models import Card, slugify, utcnow
from .scheduler import DEFAULT_SETTINGS

DECK_VERSION = 1


class DeckError(Exception):
    pass


def _str_presenter(dumper, data):
    """Dump multi-line strings as block literals so the file stays readable."""
    if "\n" in data:
        return dumper.represent_scalar("tag:yaml.org,2002:str", data, style="|")
    return dumper.represent_scalar("tag:yaml.org,2002:str", data)


class _Dumper(yaml.SafeDumper):
    pass


_Dumper.add_representer(str, _str_presenter)


class Store:
    def __init__(self, path: os.PathLike | str):
        self.path = Path(path).expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._mtime: Optional[float] = None
        self.settings: Dict = dict(DEFAULT_SETTINGS)
        self.stats: Dict = {"introduced": {}}
        self.cards: List[Card] = []
        self.load(force=True)

    # ------------------------------------------------------------------- io

    def load(self, force: bool = False) -> None:
        if not self.path.exists():
            if force:
                self.settings = dict(DEFAULT_SETTINGS)
                self.stats = {"introduced": {}}
                self.cards = []
                self.save()
            return
        mtime = self.path.stat().st_mtime
        if not force and self._mtime == mtime:
            return
        try:
            raw = yaml.safe_load(self.path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as exc:
            raise DeckError(f"{self.path.name} is not valid YAML: {exc}") from exc
        if not isinstance(raw, dict):
            raise DeckError(f"{self.path.name} should contain a mapping at the top level")

        settings = dict(DEFAULT_SETTINGS)
        settings.update(raw.get("settings") or {})
        self.settings = settings
        stats = raw.get("stats") or {}
        stats.setdefault("introduced", {})
        self.stats = stats
        self.cards = [Card.from_dict(c) for c in (raw.get("cards") or [])]
        self._mtime = mtime

    def reload_if_changed(self) -> None:
        try:
            self.load(force=False)
        except FileNotFoundError:
            self.load(force=True)

    def save(self) -> None:
        payload = {
            "version": DECK_VERSION,
            "settings": _clean_settings(self.settings),
            "stats": self.stats,
            "cards": [c.to_dict() for c in self.cards],
        }
        text = yaml.dump(
            payload, Dumper=_Dumper, allow_unicode=True,
            sort_keys=False, default_flow_style=False, width=100,
        )
        header = (
            "# German B1 flashcards — edit this file by hand whenever you like.\n"
            "# The running app reloads it automatically when the file changes.\n"
        )
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists():
            shutil.copy2(self.path, self.path.with_suffix(self.path.suffix + ".bak"))
        fd, tmp = tempfile.mkstemp(dir=str(self.path.parent), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(header + text)
            os.replace(tmp, self.path)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)
        self._mtime = self.path.stat().st_mtime

    # ---------------------------------------------------------------- lookup

    def by_id(self, card_id: str) -> Optional[Card]:
        return next((c for c in self.cards if c.id == card_id), None)

    def by_lemma(self, lemma: str) -> Optional[Card]:
        needle = lemma.strip().lower()
        return next((c for c in self.cards if c.lemma.strip().lower() == needle), None)

    def resolve(self, key: str) -> Optional[Card]:
        return self.by_id(key) or self.by_lemma(key)

    def search(self, needle: str) -> List[Card]:
        return [c for c in self.cards if c.matches(needle)]

    def unique_id(self, lemma: str) -> str:
        base = slugify(lemma)
        existing = {c.id for c in self.cards}
        if base not in existing:
            return base
        n = 2
        while f"{base}-{n}" in existing:
            n += 1
        return f"{base}-{n}"

    # ---------------------------------------------------------------- mutate

    def add(self, card: Card, save: bool = True) -> Card:
        if not card.id or self.by_id(card.id):
            card.id = self.unique_id(card.lemma or "karte")
        card.created = card.created or utcnow()
        card.modified = utcnow()
        self.cards.append(card)
        if save:
            self.save()
        return card

    def update(self, card_id: str, fields: Dict, save: bool = True) -> Card:
        card = self.by_id(card_id)
        if not card:
            raise DeckError(f"no card with id {card_id!r}")
        merged = card.to_dict()
        for key, value in fields.items():
            if key in ("id", "srs", "created"):
                continue
            merged[key] = value
        updated = Card.from_dict(merged)
        updated.id = card.id
        updated.created = card.created
        updated.srs = card.srs
        updated.modified = utcnow()
        self.cards[self.cards.index(card)] = updated
        if save:
            self.save()
        return updated

    def delete(self, card_id: str, save: bool = True) -> bool:
        card = self.by_id(card_id)
        if not card:
            return False
        self.cards.remove(card)
        if save:
            self.save()
        return True

    def note_introduced(self, when: Optional[dt.datetime] = None, save: bool = False) -> None:
        day = (when or utcnow()).astimezone().date().isoformat()
        counts = self.stats.setdefault("introduced", {})
        counts[day] = int(counts.get(day, 0)) + 1
        if save:
            self.save()

    def introduced_today(self, when: Optional[dt.datetime] = None) -> int:
        day = (when or utcnow()).astimezone().date().isoformat()
        return int((self.stats.get("introduced") or {}).get(day, 0))

    # -------------------------------------------------------- import / export

    def export_text(self) -> str:
        return self.path.read_text(encoding="utf-8") if self.path.exists() else ""

    def import_cards(self, raw_yaml: str, mode: str = "merge") -> Dict[str, int]:
        """Import a deck file or a bare list of cards.

        mode="merge"   keep existing cards, skip duplicate lemmas
        mode="replace" wipe the deck first
        """
        try:
            data = yaml.safe_load(raw_yaml) or {}
        except yaml.YAMLError as exc:
            raise DeckError(f"could not parse the imported YAML: {exc}") from exc
        incoming: Iterable
        if isinstance(data, dict):
            incoming = data.get("cards") or []
            if mode == "replace" and data.get("settings"):
                self.settings.update(data["settings"])
        elif isinstance(data, list):
            incoming = data
        else:
            raise DeckError("expected a deck mapping or a list of cards")

        if mode == "replace":
            self.cards = []

        added = skipped = 0
        for raw in incoming:
            card = Card.from_dict(raw)
            if not card.lemma:
                skipped += 1
                continue
            if mode != "replace" and self.by_lemma(card.lemma):
                skipped += 1
                continue
            card.id = self.unique_id(card.lemma)
            self.cards.append(card)
            added += 1
        self.save()
        return {"added": added, "skipped": skipped, "total": len(self.cards)}

    # ------------------------------------------------------------- settings

    def set_settings(self, values: Dict, save: bool = True) -> Dict:
        for key, value in values.items():
            if key in DEFAULT_SETTINGS or key == "target_date":
                self.settings[key] = value
        if save:
            self.save()
        return self.settings

    def known_lemmas(self, limit: int = 150) -> List[str]:
        """Vocabulary the generator should try to reuse in new example sentences.

        Best-known first: cards with the longest intervals are the ones the user
        actually remembers, so reusing them makes new sentences comprehensible.
        """
        seen = sorted(
            [c for c in self.cards if c.srs.state != "new" and c.lemma],
            key=lambda c: (-c.srs.interval_days, -c.srs.reps),
        )
        rest = [c for c in self.cards if c.srs.state == "new" and c.lemma]
        out: List[str] = []
        for card in seen + rest:
            out.append(card.headword if card.type == "noun" else card.lemma)
            if len(out) >= limit:
                break
        return out

    def grammar_topics(self, limit: int = 40) -> List[str]:
        return [c.lemma for c in self.cards if c.type == "grammar"][:limit]


def _clean_settings(settings: Dict) -> Dict:
    """Keep settings in a stable, readable order in the YAML file."""
    order = list(DEFAULT_SETTINGS.keys())
    out = {}
    for key in order:
        if key in settings:
            value = settings[key]
            if isinstance(value, (dt.date, dt.datetime)):
                value = value.isoformat()[:10]
            out[key] = value
    for key, value in settings.items():
        if key not in out:
            out[key] = value
    return out
