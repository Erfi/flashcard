"""Deadline-aware SM-2 scheduling.

Plain SM-2 optimises for long-term retention: intervals grow multiplicatively
and quickly exceed a few weeks. That is the wrong shape when you are studying
towards a fixed date — a card added a week before the exam would only be seen
twice.

This scheduler keeps SM-2's grading and ease logic but adds two things:

1. Intraday learning steps (minutes/hours), because several study blocks a day
   means a card can profitably come back the same afternoon.
2. An interval cap derived from the days remaining until `target_date`:
   no interval is ever longer than `days_left / reviews_before_target`, so
   every card keeps getting spaced touches inside the horizon.

Once the target date has passed (or if none is set) the cap disappears and the
behaviour is ordinary SM-2.
"""

from __future__ import annotations

import datetime as dt
import math
import random
from typing import Dict, List, Optional, Tuple

from .models import (AGAIN, EASY, FORWARD, GOOD, GRAMMAR, HARD, LEARNING, NEW,
                     RELEARNING, REVERSE, REVIEW, SRS, Card, utcnow)

DAY = 24 * 60.0  # minutes in a day

DEFAULT_SETTINGS: Dict[str, object] = {
    "target_date": None,               # "2026-09-18" or None
    "reviews_before_target": 3.0,      # aim for ~this many more reps before the date
    "learning_steps_minutes": [10, 60, 240],
    "relearning_steps_minutes": [10, 90],
    "graduating_interval_days": 1.0,
    "easy_interval_days": 2.0,
    "starting_ease": 2.5,
    "min_ease": 1.3,
    "hard_multiplier": 1.2,
    "easy_bonus": 1.3,
    "lapse_multiplier": 0.5,
    "max_interval_days": 180.0,
    "min_interval_days": 0.25,         # 6 hours
    "daily_new_limit": 40,             # 0 = unlimited
    "fuzz": True,                      # jitter long intervals so cards don't clump
    "shuffle": True,                   # randomise order among equally urgent cards
    "grammar_enabled": False,          # grammar cards in the study queue (off by default)
    "reverse_enabled": True,           # also study meaning -> word
    "reverse_unlock_interval_days": 3.0,  # ... once recognition has matured this far
}


def _as_date(value) -> Optional[dt.date]:
    if not value:
        return None
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    try:
        return dt.date.fromisoformat(str(value).strip()[:10])
    except ValueError:
        return None


class Scheduler:
    def __init__(self, settings: Optional[Dict] = None):
        self.s: Dict = dict(DEFAULT_SETTINGS)
        if settings:
            self.s.update({k: v for k, v in settings.items() if v is not None or k == "target_date"})

    # ------------------------------------------------------------ deadline cap

    def days_left(self, now: Optional[dt.datetime] = None) -> Optional[float]:
        target = _as_date(self.s.get("target_date"))
        if not target:
            return None
        now = now or utcnow()
        delta = (target - now.date()).days
        return float(delta)

    def interval_cap(self, now: Optional[dt.datetime] = None) -> float:
        """Longest interval we are willing to schedule right now."""
        hard_cap = float(self.s["max_interval_days"])
        left = self.days_left(now)
        if left is None or left <= 0:
            return hard_cap
        splits = max(1.0, float(self.s["reviews_before_target"]))
        return max(float(self.s["min_interval_days"]), min(hard_cap, left / splits))

    def _clamp(self, interval_days: float, now: dt.datetime) -> float:
        lo = float(self.s["min_interval_days"])
        return max(lo, min(interval_days, self.interval_cap(now)))

    def _fuzz(self, interval_days: float) -> float:
        """Spread long intervals by up to 5% so batches don't come back together."""
        if not self.s.get("fuzz") or interval_days < 4:
            return interval_days
        # deterministic-ish jitter, no RNG state to persist
        offset = (hash(round(interval_days, 3)) % 100) / 100.0  # 0..1
        return interval_days * (0.95 + 0.10 * offset)

    # ------------------------------------------------------------------ steps

    def _steps(self, state: str) -> List[float]:
        key = "relearning_steps_minutes" if state == RELEARNING else "learning_steps_minutes"
        steps = [float(x) for x in self.s[key]] or [10.0]
        return steps

    # ----------------------------------------------------------------- answer

    def answer(self, card: Card, grade: int, now: Optional[dt.datetime] = None,
               direction: str = FORWARD) -> Card:
        """Apply a grade to one direction of a card, mutating its SRS state.

        The two directions are scheduled independently: recognising a word and
        producing it are different skills, so they get their own ease, interval
        and lapse count.
        """
        now = now or utcnow()
        srs = card.srs_for(direction)
        grade = int(grade)
        if grade not in (AGAIN, HARD, GOOD, EASY):
            raise ValueError(f"unknown grade: {grade}")

        if srs.state in (NEW, LEARNING, RELEARNING):
            self._answer_learning(srs, grade, now)
        else:
            self._answer_review(srs, grade, now)

        srs.reps += 1
        srs.last_review = now
        srs.last_grade = grade
        card.modified = now
        return card

    def _answer_learning(self, srs: SRS, grade: int, now: dt.datetime) -> None:
        state = RELEARNING if srs.state == RELEARNING else LEARNING
        steps = self._steps(state)

        if grade == AGAIN:
            srs.state = state
            srs.step = 0
            srs.due = now + dt.timedelta(minutes=steps[0])
            return

        if grade == HARD:
            srs.state = state
            idx = min(srs.step, len(steps) - 1)
            srs.due = now + dt.timedelta(minutes=steps[idx] * 1.5)
            return

        if grade == EASY:
            self._graduate(srs, float(self.s["easy_interval_days"]), now)
            return

        # GOOD -> advance one step, graduate off the end
        next_step = srs.step + 1
        if next_step < len(steps):
            srs.state = state
            srs.step = next_step
            srs.due = now + dt.timedelta(minutes=steps[next_step])
            return

        base = float(self.s["graduating_interval_days"])
        if srs.state == RELEARNING and srs.interval_days:
            # returning from a lapse: resume at the reduced interval we stored
            base = max(base, srs.interval_days)
        self._graduate(srs, base, now)

    def _graduate(self, srs: SRS, interval_days: float, now: dt.datetime) -> None:
        srs.state = REVIEW
        srs.step = 0
        srs.interval_days = self._clamp(interval_days, now)
        srs.due = now + dt.timedelta(days=srs.interval_days)

    def _answer_review(self, srs: SRS, grade: int, now: dt.datetime) -> None:
        ease = srs.ease or float(self.s["starting_ease"])
        interval = max(srs.interval_days, float(self.s["min_interval_days"]))

        if grade == AGAIN:
            srs.lapses += 1
            srs.ease = max(float(self.s["min_ease"]), ease - 0.20)
            # remember a shortened interval to resume with once relearning ends
            srs.interval_days = self._clamp(interval * float(self.s["lapse_multiplier"]), now)
            srs.state = RELEARNING
            srs.step = 0
            srs.due = now + dt.timedelta(minutes=self._steps(RELEARNING)[0])
            return

        if grade == HARD:
            srs.ease = max(float(self.s["min_ease"]), ease - 0.15)
            nxt = interval * float(self.s["hard_multiplier"])
        elif grade == GOOD:
            srs.ease = ease
            nxt = interval * ease
        else:  # EASY
            srs.ease = ease + 0.15
            nxt = interval * ease * float(self.s["easy_bonus"])

        nxt = self._fuzz(nxt)
        srs.state = REVIEW
        srs.step = 0
        srs.interval_days = self._clamp(nxt, now)
        srs.due = now + dt.timedelta(days=srs.interval_days)

    # ---------------------------------------------------------------- preview

    def preview(self, card: Card, now: Optional[dt.datetime] = None,
                direction: str = FORWARD) -> Dict[str, str]:
        """Human labels for the four answer buttons, without mutating the card."""
        import copy

        now = now or utcnow()
        out: Dict[str, str] = {}
        for grade in (AGAIN, HARD, GOOD, EASY):
            probe = copy.deepcopy(card)
            self.answer(probe, grade, now, direction)
            due = probe.srs_for(direction).due
            out[str(grade)] = humanise((due - now).total_seconds() if due else 0)
        return out


def humanise(seconds: float) -> str:
    minutes = seconds / 60.0
    if minutes < 60:
        return f"{max(1, round(minutes))} Min."
    hours = minutes / 60.0
    if hours < 24:
        return f"{hours:.0f} Std." if hours >= 2 else "1 Std."
    days = hours / 24.0
    if days < 30:
        return f"{days:.0f} T." if days >= 1.5 else "1 T."
    return f"{days / 30.0:.1f} Mon."


def is_due(card: Card, now: Optional[dt.datetime] = None,
           direction: str = FORWARD) -> bool:
    now = now or utcnow()
    srs = card.srs_for(direction)
    if srs.state == NEW or srs.due is None:
        return True
    return srs.due <= now


def reverse_unlocked(card: Card, unlock_interval_days: float = 3.0) -> bool:
    """Is the production direction ready to be studied?

    Producing a word you cannot yet recognise is mostly frustration, so the
    reverse direction stays out of the queue until the forward direction has
    reached a real review interval. Once its own scheduling has started, it
    keeps running on its own.
    """
    if not card.supports_reverse:
        return False
    if card.srs_reverse.reps:
        return True
    return card.srs.state == REVIEW and card.srs.interval_days >= unlock_interval_days


def _shuffled(items: List, key, rng: random.Random) -> List:
    """Shuffle inside each group of equal priority, keep the groups in order."""
    groups: Dict = {}
    for item in items:
        groups.setdefault(key(item), []).append(item)
    out: List = []
    for group_key in sorted(groups):
        bucket = groups[group_key]
        rng.shuffle(bucket)
        out.extend(bucket)
    return out


def build_queue(cards: List[Card], settings: Dict, now: Optional[dt.datetime] = None,
                introduced_today: int = 0,
                rng: Optional[random.Random] = None) -> List[Tuple[Card, str]]:
    """Order the study queue as (card, direction) pairs.

    Learning cards come first because their intervals are minutes long and
    delaying them wastes the step. Then due reviews, most overdue first, then
    new material. With `shuffle` on, cards of equal urgency come in random
    order instead of alphabetically or by creation date — a whole theme in a
    row is easy in a way that does not survive contact with real German.
    """
    now = now or utcnow()
    rng = rng or random.Random()
    shuffle = bool(settings.get("shuffle", True))
    reverse_on = bool(settings.get("reverse_enabled", True))
    grammar_on = bool(settings.get("grammar_enabled", False))
    unlock = float(settings.get("reverse_unlock_interval_days", 3.0))

    learning: List[Tuple[Card, str]] = []
    review: List[Tuple[Card, str]] = []
    new_reverse: List[Tuple[Card, str]] = []
    new_forward: List[Tuple[Card, str]] = []

    for card in cards:
        if card.type == GRAMMAR and not grammar_on:
            # a rule with worked examples reads better than it drills; keep those
            # cards in the deck for reference and out of the review queue
            continue
        for direction in (FORWARD, REVERSE):
            if direction == REVERSE and not (reverse_on and reverse_unlocked(card, unlock)):
                continue
            srs = card.srs_for(direction)
            item = (card, direction)
            if srs.state == NEW or srs.due is None:
                (new_reverse if direction == REVERSE else new_forward).append(item)
            elif srs.due <= now:
                (learning if srs.state in (LEARNING, RELEARNING) else review).append(item)

    learning.sort(key=lambda it: it[0].srs_for(it[1]).due or now)
    if shuffle:
        # equal priority = same whole day of lateness for reviews, and for new
        # material nothing distinguishes one card from another at all
        review = _shuffled(review, lambda it: -((now - (it[0].srs_for(it[1]).due or now)).days), rng)
        rng.shuffle(new_reverse)
        rng.shuffle(new_forward)
    else:
        review.sort(key=lambda it: it[0].srs_for(it[1]).due or now)
        new_reverse.sort(key=lambda it: (it[0].created or now))
        new_forward.sort(key=lambda it: (it[0].created or now))

    # A freshly unlocked production card is a second pass over a word you
    # already know, so it outranks a word you have never seen. Without this,
    # a large backlog of new vocabulary starves the production direction
    # entirely once the daily limit bites.
    new = new_reverse + new_forward

    limit = int(settings.get("daily_new_limit", 0) or 0)
    if limit:
        new = new[: max(0, limit - introduced_today)]

    return learning + review + new


def projection(cards: List[Card], settings: Dict, now: Optional[dt.datetime] = None) -> Dict:
    """Rough answer to 'will I actually get through this before the target date?'"""
    now = now or utcnow()
    sched = Scheduler(settings)
    left = sched.days_left(now)
    unlock = float(settings.get("reverse_unlock_interval_days", 3.0))
    reverse_on = bool(settings.get("reverse_enabled", True))

    grammar_on = bool(settings.get("grammar_enabled", False))
    grammar_total = sum(1 for c in cards if c.type == GRAMMAR)
    studied = [c for c in cards if grammar_on or c.type != GRAMMAR]

    total = len(cards)
    mature = sum(1 for c in studied if c.srs.state == REVIEW and c.srs.interval_days >= 3)
    unseen = sum(1 for c in studied if c.srs.state == NEW)
    reverse_possible = sum(1 for c in cards if c.supports_reverse) if reverse_on else 0
    reverse_open = sum(1 for c in cards if reverse_on and reverse_unlocked(c, unlock))
    reverse_started = sum(1 for c in cards if c.srs_reverse.reps)

    per_day = None
    if left and left > 0 and unseen:
        per_day = math.ceil(unseen / left)
    return {
        "days_left": left,
        "total": total,
        "in_rotation": len(studied),
        "grammar_total": grammar_total,
        "grammar_enabled": grammar_on,
        "unseen": unseen,
        "mature": mature,
        "interval_cap_days": round(sched.interval_cap(now), 2),
        "new_per_day_needed": per_day,
        "reverse_possible": reverse_possible,
        "reverse_open": reverse_open,
        "reverse_started": reverse_started,
    }
