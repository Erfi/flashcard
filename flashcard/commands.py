"""Parser for the command bar (`add katze`, `set target 2026-09-18`, ...)."""

from __future__ import annotations

import re
from typing import Dict

TYPE_ALIASES = {
    "noun": "noun", "nomen": "noun", "substantiv": "noun", "n": "noun",
    "verb": "verb", "v": "verb",
    "adjective": "adjective", "adjektiv": "adjective", "adj": "adjective",
    "adverb": "adverb", "adv": "adverb",
    "phrase": "phrase", "redewendung": "phrase", "wendung": "phrase",
    "grammar": "grammar", "grammatik": "grammar", "g": "grammar",
}

HELP = [
    ("add <Wort>", "Karte anlegen, Typ wird automatisch erkannt — z. B. `add katze`"),
    ("add verb: laufen", "Typ vorgeben (noun, verb, adjective, adverb, phrase, grammar)"),
    ("add grammar: Konjunktiv II", "Grammatikkarte anlegen"),
    ("satz <Wort>", "neuen Beispielsatz für eine Karte erzeugen"),
    ("edit <Wort>", "Karte im Editor öffnen"),
    ("del <Wort>", "Karte löschen"),
    ("find <Text>", "Karten durchsuchen"),
    ("lernen", "zur Lernansicht wechseln"),
    ("stats", "Statistik und Prognose bis zum Zieldatum"),
    ("set target 2026-09-18", "Zieldatum setzen (steuert die Intervall-Obergrenze)"),
    ("set new 40", "neue Karten pro Tag"),
    ("set reviews 3", "gewünschte Wiederholungen pro Karte bis zum Zieldatum"),
    ("export", "Deck als YAML herunterladen"),
    ("help", "diese Liste"),
]


def parse(text: str) -> Dict:
    """Turn a command-bar string into an action dict."""
    raw = (text or "").strip()
    if not raw:
        return {"action": "noop"}

    lowered = raw.lower()
    first, _, rest = raw.partition(" ")
    first_l = first.lower()
    rest = rest.strip()

    if first_l in ("help", "hilfe", "?"):
        return {"action": "help", "entries": HELP}

    if first_l in ("add", "neu", "new", "+"):
        if not rest:
            return {"action": "error", "message": "Was soll ich anlegen? z. B. `add katze`"}
        hint = ""
        match = re.match(r"^([A-Za-zÄÖÜäöü]+)\s*:\s*(.+)$", rest)
        if match and match.group(1).lower() in TYPE_ALIASES:
            hint = TYPE_ALIASES[match.group(1).lower()]
            rest = match.group(2).strip()
        else:
            head, _, tail = rest.partition(" ")
            if head.lower() in TYPE_ALIASES and tail.strip():
                hint = TYPE_ALIASES[head.lower()]
                rest = tail.strip()
        return {"action": "add", "query": rest, "hint_type": hint}

    if first_l in ("satz", "sentence", "beispiel"):
        if not rest:
            return {"action": "error", "message": "Für welche Karte? z. B. `satz katze`"}
        return {"action": "sentence", "key": rest}

    if first_l in ("edit", "bearbeiten"):
        return {"action": "edit", "key": rest}

    if first_l in ("del", "delete", "rm", "löschen", "loeschen"):
        if not rest:
            return {"action": "error", "message": "Welche Karte soll weg? z. B. `del katze`"}
        return {"action": "delete", "key": rest}

    if first_l in ("find", "suche", "search", "/"):
        return {"action": "find", "query": rest}

    if lowered in ("review", "lernen", "learn", "study"):
        return {"action": "review"}

    if lowered in ("stats", "statistik", "status"):
        return {"action": "stats"}

    if lowered in ("browse", "karten", "cards", "liste"):
        return {"action": "browse"}

    if lowered in ("export",):
        return {"action": "export"}

    if first_l == "set":
        return _parse_set(rest)

    # bare word: treat as add, it is the most common thing you want
    if re.fullmatch(r"[\wÄÖÜäöüß\- ]{2,60}", raw):
        return {"action": "add", "query": raw, "hint_type": ""}

    return {"action": "error", "message": f"Unbekannter Befehl: {raw!r} — `help` zeigt alle."}


def _parse_set(rest: str) -> Dict:
    key, _, value = rest.partition(" ")
    key, value = key.lower().strip(), value.strip()
    if not key:
        return {"action": "error", "message": "set was? z. B. `set target 2026-09-18`"}

    if key in ("target", "ziel", "date", "zieldatum"):
        if value.lower() in ("none", "off", "aus", "-"):
            return {"action": "settings", "values": {"target_date": None}}
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            return {"action": "error", "message": "Datum bitte als JJJJ-MM-TT, z. B. 2026-09-18"}
        return {"action": "settings", "values": {"target_date": value}}

    if key in ("new", "neu", "daily_new_limit"):
        try:
            return {"action": "settings", "values": {"daily_new_limit": max(0, int(value))}}
        except ValueError:
            return {"action": "error", "message": "Zahl erwartet, z. B. `set new 40`"}

    if key in ("reviews", "reps", "reviews_before_target"):
        try:
            number = float(value)
        except ValueError:
            return {"action": "error", "message": "Zahl erwartet, z. B. `set reviews 3`"}
        return {"action": "settings", "values": {"reviews_before_target": max(1.0, number)}}

    if key in ("max", "max_interval", "max_interval_days"):
        try:
            return {"action": "settings", "values": {"max_interval_days": float(value)}}
        except ValueError:
            return {"action": "error", "message": "Zahl erwartet, z. B. `set max 90`"}

    return {"action": "error", "message": f"Unbekannte Einstellung: {key!r}"}
