"""Turn an example sentence into a gap for the production direction.

On a reverse card you are asked to come up with the word, so the example must
not contain it. That is harder than a plain string replace: the sentence holds
an inflected form (Katzen, läuft, lief, aufgestanden), a separable verb split
across the clause, or a reflexive pronoun. This module blanks out every form it
can recognise, and — for nouns — the article in front of it, since the article
is part of what you are supposed to produce.
"""

from __future__ import annotations

import re
from typing import Iterable, List

from .models import Card

BLANK = "____"
ARTICLES = r"(?:der|die|das|den|dem|des|ein|eine|einen|einem|einer|eines|" \
           r"mein|meine|meinen|meinem|meiner|kein|keine|keinen|keinem|keiner)"
# endings we peel off to reach a stem that also matches inflected forms
ENDINGS = ("ungen", "enden", "erin", "eren", "esten", "ende", "est", "ern",
           "end", "ung", "en", "er", "es", "em", "st", "e", "n", "s", "t")
STOP = {"sich", "hat", "ist", "die", "der", "das", "zu", "es"}


PARTICLES = ("ab", "an", "auf", "aus", "bei", "durch", "ein", "her", "hin", "los",
             "mit", "nach", "vor", "weg", "zu", "zurück", "zusammen", "um", "über")


def _forms(card: Card) -> List[str]:
    """Every surface form of this card's headword that might appear in a sentence."""
    raw: List[str] = [card.lemma, card.plural, card.praesens_3sg, card.praeteritum,
                      card.perfekt]
    out: List[str] = []
    for item in raw:
        text = (item or "").strip()
        if not text:
            continue
        text = re.sub(r"^(die|der|das)\s+", "", text)      # plural comes with its article
        text = re.sub(r"^(hat|ist)\s+", "", text)          # perfekt comes with its auxiliary
        text = re.sub(r"^sich\s+", "", text)
        for token in text.split():
            token = token.strip(".,;:!?()")
            if len(token) >= 3 and token.lower() not in STOP and token.lower() not in PARTICLES:
                out.append(token)

    if card.type == "verb":
        # the bare present stem: "anrufen" -> "anruf", and for a separable verb
        # also the stem without its prefix, because the sentence splits them
        # ("Ruf mich bitte an")
        base = re.sub(r"^sich\s+", "", card.lemma).strip()
        stem = re.sub(r"(en|n)$", "", base)
        if len(stem) >= 3:
            out.append(stem)
            for particle in sorted(PARTICLES, key=len, reverse=True):
                if stem.lower().startswith(particle) and len(stem) - len(particle) >= 3:
                    out.append(stem[len(particle):])
                    break
    # longest first so "aufgestanden" wins over "auf"
    return sorted(set(out), key=len, reverse=True)


def _particle(card: Card) -> str:
    """The separable prefix, which sits at the end of the clause on its own."""
    if card.type != "verb" or not card.separable:
        return ""
    base = re.sub(r"^sich\s+", "", card.lemma).strip().lower()
    for particle in sorted(PARTICLES, key=len, reverse=True):
        if base.startswith(particle) and len(base) > len(particle) + 2:
            return particle
    return ""


def _pattern(form: str) -> str:
    """A regex for this form plus the inflections built on the same stem."""
    escaped = re.escape(form)
    if len(form) < 4:
        return rf"{escaped}(?:e|en|es|s|er|n)?"
    stem = form
    for ending in ENDINGS:
        if stem.lower().endswith(ending) and len(stem) - len(ending) >= 4:
            stem = stem[: -len(ending)]
            break
    variants = {stem}
    # -eln/-ern verbs drop the e when conjugated: bezweifeln -> bezweifle
    if len(stem) > 4 and stem[-2:].lower() in ("el", "er"):
        variants.add(stem[:-2] + stem[-1])
    return "|".join(rf"{re.escape(v)}\w*" for v in sorted(variants, key=len, reverse=True))


def mask(card: Card, text: str | None = None) -> str:
    """Replace the card's word (and a noun's article) in the sentence with a blank."""
    sentence = (text if text is not None else card.example) or ""
    if not sentence:
        return ""
    forms = _forms(card)
    if not forms:
        return sentence

    body = "|".join(_pattern(f) for f in forms)
    if card.type == "noun":
        # the article belongs to the answer, so it disappears with the noun
        pattern = re.compile(rf"\b(?:{ARTICLES}\s+)?(?:{body})\b", re.IGNORECASE)
    else:
        pattern = re.compile(rf"\b(?:{body})\b", re.IGNORECASE)

    masked = pattern.sub(BLANK, sentence)
    particle = _particle(card)
    if particle and BLANK in masked:
        # only when it stands at the end of its clause, so real prepositions survive
        masked = re.sub(rf"\s\b{re.escape(particle)}\b(?=\s*[.,;!?]|$)", "", masked,
                        flags=re.IGNORECASE)
    masked = re.sub(rf"(?:{BLANK}\s+){{1,}}{BLANK}", BLANK, masked)  # collapse neighbours
    if masked == sentence:
        return sentence
    # a sentence that became only blanks teaches nothing — fall back
    if masked.replace(BLANK, "").strip(" .,!?") == "":
        return sentence
    return masked[0].upper() + masked[1:] if masked[:1].islower() else masked


def mask_all(card: Card, sentences: Iterable[str]) -> List[str]:
    return [mask(card, s) for s in sentences]
