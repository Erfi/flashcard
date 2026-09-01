"""Review history: an append-only log, and the series the Statistik tab plots.

The deck file holds only a snapshot of each card — current interval, ease, reps —
so nothing in it can answer "how did last week go?". This module adds a sidecar
CSV, one row per answer, which keeps `deck.yaml` small and hand-editable while
still giving us a real history. The CSV opens in any spreadsheet.

Two of the series below (`forecast`, `intervals`) are computed from the deck
alone and work from the first run. The rest need the log and therefore start on
the day logging began — we reconstruct backwards from today's true numbers
rather than inventing anything for the cards studied before that.
"""

from __future__ import annotations

import csv
import datetime as dt
import os
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence

from .models import (FORWARD, GOOD, GRAMMAR, NEW, REVERSE, REVIEW, Card,
                     parse_ts, utcnow)

FIELDS = ["ts", "card", "direction", "grade", "state_before", "state_after",
          "interval_before", "interval_after", "ease_after", "lapses_after"]

MATURE_DAYS = 3.0          # the same threshold the "gefestigt" stat uses
GRADE_NAMES = {0: "again", 1: "hard", 2: "good", 3: "easy"}


def local_date(value) -> Optional[dt.date]:
    """The calendar day a review belongs to, in the studier's own timezone."""
    stamp = parse_ts(value)
    return stamp.astimezone().date() if stamp else None


class ReviewLog:
    def __init__(self, path: os.PathLike | str):
        self.path = Path(path)

    # ------------------------------------------------------------------ write

    def append(self, card: Card, direction: str, grade: int,
               state_before: str, interval_before: float,
               when: Optional[dt.datetime] = None) -> None:
        srs = card.srs_for(direction)
        row = {
            "ts": (when or utcnow()).isoformat(),
            "card": card.id,
            "direction": direction,
            "grade": int(grade),
            "state_before": state_before,
            "state_after": srs.state,
            "interval_before": round(float(interval_before), 3),
            "interval_after": round(float(srs.interval_days), 3),
            "ease_after": round(float(srs.ease), 2),
            "lapses_after": srs.lapses,
        }
        new_file = not self.path.exists()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=FIELDS)
            if new_file:
                writer.writeheader()
            writer.writerow(row)

    # ------------------------------------------------------------------- read

    def rows(self, since: Optional[dt.date] = None) -> List[Dict]:
        """Every logged answer, oldest first. Malformed lines are skipped."""
        if not self.path.exists():
            return []
        out: List[Dict] = []
        with self.path.open("r", encoding="utf-8", newline="") as handle:
            for raw in csv.DictReader(handle):
                day = local_date(raw.get("ts"))
                if day is None or (since and day < since):
                    continue
                try:
                    out.append({
                        "ts": raw["ts"], "day": day, "card": raw.get("card", ""),
                        "direction": REVERSE if raw.get("direction") == REVERSE else FORWARD,
                        "grade": int(raw.get("grade") or 0),
                        "state_before": raw.get("state_before") or NEW,
                        "state_after": raw.get("state_after") or NEW,
                        "interval_before": float(raw.get("interval_before") or 0),
                        "interval_after": float(raw.get("interval_after") or 0),
                    })
                except (TypeError, ValueError):
                    continue
        out.sort(key=lambda r: r["ts"])
        return out


# --------------------------------------------------------------------- series

def _days(end: dt.date, count: int) -> List[dt.date]:
    return [end - dt.timedelta(days=i) for i in range(count - 1, -1, -1)]


def activity(rows: Sequence[Dict], days: int, today: dt.date,
             introduced: Optional[Dict[str, int]] = None) -> List[Dict]:
    """Per day: how much was reviewed, how it was graded, how much was new."""
    window = _days(today, days)
    blank = {"reviews": 0, "again": 0, "hard": 0, "good": 0, "easy": 0,
             "new": 0, "graded": 0, "correct": 0}
    table = {day: dict(blank) for day in window}
    for row in rows:
        bucket = table.get(row["day"])
        if bucket is None:
            continue
        bucket["reviews"] += 1
        bucket[GRADE_NAMES.get(row["grade"], "good")] += 1
        if row["state_before"] == NEW:
            bucket["new"] += 1
        elif row["state_before"] == REVIEW:
            # retention is about cards you had learned, not ones still in steps
            bucket["graded"] += 1
            bucket["correct"] += 1 if row["grade"] >= GOOD else 0
    # days before the log existed still know how many cards were introduced
    for day, bucket in table.items():
        if not bucket["reviews"] and introduced:
            bucket["new"] = int(introduced.get(day.isoformat(), 0) or 0)
    return [{"date": day.isoformat(), **table[day]} for day in window]


def retention(daily: Sequence[Dict], window: int = 7) -> List[Dict]:
    """Rolling share of already-learned cards answered Gut or better."""
    out = []
    for i, day in enumerate(daily):
        chunk = daily[max(0, i - window + 1): i + 1]
        graded = sum(d["graded"] for d in chunk)
        correct = sum(d["correct"] for d in chunk)
        out.append({"date": day["date"],
                    "value": round(100 * correct / graded, 1) if graded else None,
                    "graded": graded})
    return out


def learned_curve(rows: Sequence[Dict], cards: Sequence[Card], days: int,
                  today: dt.date, threshold: float = MATURE_DAYS) -> List[Dict]:
    """Cards past the maturity threshold, per day and per direction.

    Reconstructed by walking the log backwards from today's true counts, so the
    line ends on the number the deck actually shows. Before the log starts it is
    flat — that is the honest shape, not a claim that nothing happened.
    """
    window = _days(today, days)
    counts = {FORWARD: 0, REVERSE: 0}
    for card in cards:
        for direction in (FORWARD, REVERSE):
            srs = card.srs_for(direction)
            if srs.state == REVIEW and srs.interval_days >= threshold:
                counts[direction] += 1

    # net crossings per day, so we can rewind
    delta = {day: {FORWARD: 0, REVERSE: 0} for day in window}
    for row in rows:
        if row["day"] not in delta:
            continue
        crossed_up = row["interval_before"] < threshold <= row["interval_after"]
        crossed_down = row["interval_after"] < threshold <= row["interval_before"]
        if crossed_up:
            delta[row["day"]][row["direction"]] += 1
        elif crossed_down:
            delta[row["day"]][row["direction"]] -= 1

    series: List[Dict] = []
    running = dict(counts)
    for day in reversed(window):
        series.append({"date": day.isoformat(),
                       "forward": running[FORWARD], "reverse": running[REVERSE]})
        running[FORWARD] -= delta[day][FORWARD]
        running[REVERSE] -= delta[day][REVERSE]
    series.reverse()
    return series


def forecast(cards: Sequence[Card], settings: Dict, days: int,
             now: Optional[dt.datetime] = None) -> List[Dict]:
    """What is already scheduled for each of the next N days."""
    now = now or utcnow()
    today = now.astimezone().date()
    window = _days(today + dt.timedelta(days=days - 1), days)
    grammar_on = bool(settings.get("grammar_enabled", False))
    table = {day: {"forward": 0, "reverse": 0} for day in window}

    for card in cards:
        if card.type == GRAMMAR and not grammar_on:
            continue
        for direction in (FORWARD, REVERSE):
            srs = card.srs_for(direction)
            if srs.state == NEW or not srs.due:
                continue
            if direction == REVERSE and not srs.reps:
                continue
            day = srs.due.astimezone().date()
            if day < today:
                day = today                      # overdue lands on today's bar
            bucket = table.get(day)
            if bucket is not None:
                bucket[direction] += 1
    return [{"date": day.isoformat(), **table[day]} for day in window]


def intervals(cards: Sequence[Card], settings: Dict) -> List[Dict]:
    """How mature the deck is: cards per interval band, both directions."""
    grammar_on = bool(settings.get("grammar_enabled", False))
    bands = [("neu", lambda i, s: s == NEW),
             ("im Lernen", lambda i, s: s in ("learning", "relearning")),
             ("1–3 T.", lambda i, s: i < 4),
             ("4–7 T.", lambda i, s: i < 8),
             ("1–2 Wo.", lambda i, s: i < 15),
             ("> 2 Wo.", lambda i, s: True)]
    out = [{"label": name, "count": 0} for name, _ in bands]
    for card in cards:
        if card.type == GRAMMAR and not grammar_on:
            continue
        for direction in (FORWARD, REVERSE):
            srs = card.srs_for(direction)
            if direction == REVERSE and not srs.reps:
                continue                          # not unlocked yet: not a card
            for index, (_, test) in enumerate(bands):
                if test(srs.interval_days, srs.state):
                    out[index]["count"] += 1
                    break
    return out


def summary(rows: Sequence[Dict], daily: Sequence[Dict]) -> Dict:
    """Headline numbers for the stat tiles."""
    today = daily[-1] if daily else {"reviews": 0, "new": 0}
    week = daily[-7:]
    graded = sum(d["graded"] for d in week)
    correct = sum(d["correct"] for d in week)
    active = [d for d in daily if d["reviews"]]
    return {
        "reviews_today": today["reviews"],
        "new_today": today["new"],
        "reviews_week": sum(d["reviews"] for d in week),
        "retention_week": round(100 * correct / graded, 1) if graded else None,
        "graded_week": graded,
        "logged_days": len(active),
        "logged_total": len(rows),
        "logging_since": rows[0]["day"].isoformat() if rows else None,
    }
