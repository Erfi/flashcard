"""Card and scheduling-state models, plus their YAML representation.

The on-disk format is deliberately plain YAML: every field is a scalar, a list
of scalars, or a small nested mapping, so the deck stays readable and
hand-editable in any text editor.
"""

from __future__ import annotations

import datetime as dt
import re
import unicodedata
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional

# --------------------------------------------------------------------------
# card kinds
# --------------------------------------------------------------------------

NOUN = "noun"
VERB = "verb"
ADJECTIVE = "adjective"
ADVERB = "adverb"
PHRASE = "phrase"
GRAMMAR = "grammar"
OTHER = "other"

CARD_TYPES = [NOUN, VERB, ADJECTIVE, ADVERB, PHRASE, GRAMMAR, OTHER]
ARTICLES = ["der", "die", "das"]

# grades used everywhere (Anki-compatible ordering)
AGAIN, HARD, GOOD, EASY = 0, 1, 2, 3
GRADE_NAMES = {AGAIN: "again", HARD: "hard", GOOD: "good", EASY: "easy"}

NEW, LEARNING, REVIEW, RELEARNING = "new", "learning", "review", "relearning"

# A card is studied in two directions. FORWARD is recognition (word -> meaning),
# REVERSE is production (meaning -> word). Each direction keeps its own SRS
# state, because recognising a word and producing it are different skills that
# mature at different speeds.
FORWARD, REVERSE = "forward", "reverse"
DIRECTIONS = (FORWARD, REVERSE)


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0)


def parse_ts(value: Any) -> Optional[dt.datetime]:
    """Accept datetimes, ISO strings and plain dates from hand-edited YAML."""
    if value in (None, "", "null"):
        return None
    if isinstance(value, dt.datetime):
        return value if value.tzinfo else value.replace(tzinfo=dt.timezone.utc)
    if isinstance(value, dt.date):
        return dt.datetime.combine(value, dt.time(4, 0), tzinfo=dt.timezone.utc)
    text = str(value).strip().replace("Z", "+00:00")
    try:
        parsed = dt.datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=dt.timezone.utc)


def fmt_ts(value: Optional[dt.datetime]) -> Optional[str]:
    if value is None:
        return None
    return value.astimezone(dt.timezone.utc).replace(microsecond=0).isoformat()


def slugify(text: str) -> str:
    # transliterate umlauts BEFORE decomposing, otherwise "ä" is already split
    # into "a" + combining diaeresis and the replacement never matches
    text = text.lower()
    for src_ch, dst in (("ä", "ae"), ("ö", "oe"), ("ü", "ue"), ("ß", "ss")):
        text = text.replace(src_ch, dst)
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    return text or "karte"


@dataclass
class SRS:
    """Per-card scheduling state. Small enough to eyeball in the YAML file."""

    state: str = NEW
    step: int = 0                      # index into the learning/relearning steps
    ease: float = 2.5                  # SM-2 ease factor
    interval_days: float = 0.0         # current review interval
    due: Optional[dt.datetime] = None  # None = never scheduled (brand new)
    reps: int = 0
    lapses: int = 0
    last_review: Optional[dt.datetime] = None
    last_grade: Optional[int] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "state": self.state,
            "step": self.step,
            "ease": round(self.ease, 2),
            "interval_days": round(self.interval_days, 3),
            "due": fmt_ts(self.due),
            "reps": self.reps,
            "lapses": self.lapses,
            "last_review": fmt_ts(self.last_review),
            "last_grade": self.last_grade,
        }

    @classmethod
    def from_dict(cls, raw: Optional[Dict[str, Any]]) -> "SRS":
        raw = raw or {}
        state = str(raw.get("state") or NEW).lower()
        if state not in (NEW, LEARNING, REVIEW, RELEARNING):
            state = NEW
        return cls(
            state=state,
            step=int(raw.get("step") or 0),
            ease=float(raw.get("ease") or 2.5),
            interval_days=float(raw.get("interval_days") or 0.0),
            due=parse_ts(raw.get("due")),
            reps=int(raw.get("reps") or 0),
            lapses=int(raw.get("lapses") or 0),
            last_review=parse_ts(raw.get("last_review")),
            last_grade=raw.get("last_grade"),
        )


def _string_list(value: Any) -> List[str]:
    """Accept a list, or a single string with one sentence per line."""
    if not value:
        return []
    if isinstance(value, str):
        return [line.strip() for line in value.splitlines() if line.strip()]
    return [str(item).strip() for item in value if str(item).strip()]


@dataclass
class Card:
    id: str
    type: str = OTHER
    lemma: str = ""
    # nouns
    article: str = ""            # der / die / das
    plural: str = ""             # e.g. "die Katzen"
    # verbs
    praesens_3sg: str = ""       # e.g. "läuft"
    praeteritum: str = ""        # e.g. "lief"
    perfekt: str = ""            # e.g. "ist gelaufen"
    aux: str = ""                # haben / sein
    separable: bool = False
    rection: str = ""            # e.g. "warten auf + Akk."
    # everything
    definition: str = ""         # short German definition / grammar rule
    example: str = ""            # example sentence (vocabulary cards)
    examples: List[str] = field(default_factory=list)  # several sentences (grammar cards)
    notes: str = ""              # free-form, never touched by the generator
    tags: List[str] = field(default_factory=list)
    level: str = "B1"
    source: str = ""             # "claude", "manual", "seed"
    created: Optional[dt.datetime] = None
    modified: Optional[dt.datetime] = None
    srs: SRS = field(default_factory=SRS)
    srs_reverse: SRS = field(default_factory=SRS)

    # ---------------------------------------------------------------- display

    @property
    def color_key(self) -> str:
        """What drives the card's colour in the UI."""
        if self.type == NOUN and self.article in ARTICLES:
            return self.article
        return self.type

    @property
    def headword(self) -> str:
        if self.type == NOUN and self.article:
            return f"{self.article} {self.lemma}"
        return self.lemma

    @property
    def supports_reverse(self) -> bool:
        """Grammar cards have no 'word' to produce, so they stay one-directional."""
        return self.type != GRAMMAR and bool(self.definition or self.example)

    def srs_for(self, direction: str = FORWARD) -> SRS:
        return self.srs_reverse if direction == REVERSE else self.srs

    def matches(self, needle: str) -> bool:
        needle = needle.lower().strip()
        haystack = " ".join(
            [self.lemma, self.definition, self.example, " ".join(self.examples),
             self.plural, self.praeteritum, self.perfekt, " ".join(self.tags)]
        ).lower()
        return needle in haystack

    # ------------------------------------------------------------ (de)serialise

    def to_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {"id": self.id, "type": self.type, "lemma": self.lemma}
        if self.type == NOUN:
            out["article"] = self.article
            out["plural"] = self.plural
        if self.type == VERB:
            out["praesens_3sg"] = self.praesens_3sg
            out["praeteritum"] = self.praeteritum
            out["perfekt"] = self.perfekt
            out["aux"] = self.aux
            out["separable"] = self.separable
        if self.rection:
            out["rection"] = self.rection
        out["definition"] = self.definition
        if self.example:
            out["example"] = self.example
        if self.examples:
            out["examples"] = list(self.examples)
        if not self.example and not self.examples:
            out["example"] = ""
        if self.notes:
            out["notes"] = self.notes
        out["tags"] = list(self.tags)
        out["level"] = self.level
        out["source"] = self.source
        out["created"] = fmt_ts(self.created)
        out["modified"] = fmt_ts(self.modified)
        out["srs"] = self.srs.to_dict()
        # only written once the reverse direction has actually been used, so
        # decks that never study production stay half the size
        if self.srs_reverse.reps or self.srs_reverse.state != NEW or self.srs_reverse.due:
            out["srs_reverse"] = self.srs_reverse.to_dict()
        return out

    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> "Card":
        raw = dict(raw or {})
        ctype = str(raw.get("type") or OTHER).lower()
        if ctype not in CARD_TYPES:
            ctype = OTHER
        tags = raw.get("tags") or []
        if isinstance(tags, str):
            tags = [t.strip() for t in tags.split(",") if t.strip()]
        article = str(raw.get("article") or "").strip().lower()
        if article not in ARTICLES:
            article = ""

        lemma = str(raw.get("lemma") or "").strip()
        # "die Katze" typed into the lemma field is an article plus a noun, not a
        # noun called "die Katze" — otherwise the headword reads "die die Katze"
        head, _, rest = lemma.partition(" ")
        if ctype == NOUN and head.lower() in ARTICLES and rest.strip():
            article = article or head.lower()
            lemma = rest.strip()

        return cls(
            id=str(raw.get("id") or slugify(lemma or "karte")),   # the cleaned lemma
            type=ctype,
            lemma=lemma,
            article=article,
            plural=str(raw.get("plural") or "").strip(),
            praesens_3sg=str(raw.get("praesens_3sg") or "").strip(),
            praeteritum=str(raw.get("praeteritum") or "").strip(),
            perfekt=str(raw.get("perfekt") or "").strip(),
            aux=str(raw.get("aux") or "").strip().lower(),
            separable=bool(raw.get("separable") or False),
            rection=str(raw.get("rection") or "").strip(),
            definition=str(raw.get("definition") or "").strip(),
            example=str(raw.get("example") or "").strip(),
            examples=_string_list(raw.get("examples")),
            notes=str(raw.get("notes") or "").strip(),
            tags=[str(t) for t in tags],
            level=str(raw.get("level") or "B1"),
            source=str(raw.get("source") or "manual"),
            created=parse_ts(raw.get("created")) or utcnow(),
            modified=parse_ts(raw.get("modified")) or utcnow(),
            srs=SRS.from_dict(raw.get("srs")),
            srs_reverse=SRS.from_dict(raw.get("srs_reverse")),
        )

    def to_api(self) -> Dict[str, Any]:
        """Dict for the browser: everything above plus derived display bits."""
        data = asdict(self)
        data["srs"] = self.srs.to_dict()
        data["srs_reverse"] = self.srs_reverse.to_dict()
        data["supports_reverse"] = self.supports_reverse
        data["created"] = fmt_ts(self.created)
        data["modified"] = fmt_ts(self.modified)
        data["color_key"] = self.color_key
        data["headword"] = self.headword
        return data
