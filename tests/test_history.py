"""The review log and the series derived from it."""
import datetime as dt

import pytest

from flashcard import history
from flashcard.models import FORWARD, REVERSE, SRS, Card
from flashcard.history import ReviewLog

TODAY = dt.date(2026, 9, 10)


def card(lemma="Katze", **kw) -> Card:
    base = {"type": "noun", "article": "die", "lemma": lemma,
            "definition": "Ein Haustier.", "example": "Die Katze schläft."}
    base.update(kw)
    return Card.from_dict(base)


def at(day_offset: int, hour: int = 9) -> dt.datetime:
    """A timestamp on a given day, expressed in the local timezone."""
    naive = dt.datetime.combine(TODAY + dt.timedelta(days=day_offset), dt.time(hour))
    return naive.astimezone()


def row(day_offset, *, direction=FORWARD, grade=2, state_before="review",
        before=4.0, after=10.0):
    return {"ts": at(day_offset).isoformat(), "day": (TODAY + dt.timedelta(days=day_offset)),
            "card": "katze", "direction": direction, "grade": grade,
            "state_before": state_before, "state_after": "review",
            "interval_before": before, "interval_after": after}


# ----------------------------------------------------------------------- log

def test_log_round_trip(tmp_path):
    log = ReviewLog(tmp_path / "reviews.csv")
    c = card()
    c.srs = SRS(state="review", interval_days=10, ease=2.5, lapses=1)
    log.append(c, FORWARD, 2, "review", 4.0, when=at(0))
    got = log.rows()
    assert len(got) == 1
    assert got[0]["card"] == c.id and got[0]["grade"] == 2
    assert got[0]["interval_before"] == 4.0 and got[0]["interval_after"] == 10.0
    assert got[0]["direction"] == FORWARD


def test_log_appends_without_rewriting(tmp_path):
    log = ReviewLog(tmp_path / "reviews.csv")
    c = card()
    for i in range(3):
        log.append(c, FORWARD, 2, "review", 1.0, when=at(-i))
    assert len(log.rows()) == 3
    assert log.path.read_text(encoding="utf-8").count("\n") == 4      # header + 3


def test_missing_log_is_not_an_error(tmp_path):
    assert ReviewLog(tmp_path / "nope.csv").rows() == []


def test_malformed_lines_are_skipped(tmp_path):
    path = tmp_path / "reviews.csv"
    path.write_text("ts,card,direction,grade,state_before,state_after,"
                    "interval_before,interval_after,ease_after,lapses_after\n"
                    f"{at(0).isoformat()},katze,forward,2,review,review,4,10,2.5,0\n"
                    "kaputt,,,,,,,,,\n"
                    f"{at(0).isoformat()},hund,forward,keine-zahl,review,review,4,10,2.5,0\n",
                    encoding="utf-8")
    assert len(ReviewLog(path).rows()) == 1


# ------------------------------------------------------------------ activity

def test_activity_counts_reviews_and_grades():
    rows = [row(0, grade=0), row(0, grade=2), row(0, grade=3), row(-1, grade=1)]
    daily = history.activity(rows, 3, TODAY)
    assert [d["date"] for d in daily] == ["2026-09-08", "2026-09-09", "2026-09-10"]
    assert daily[-1]["reviews"] == 3 and daily[-1]["again"] == 1 and daily[-1]["easy"] == 1
    assert daily[-2]["hard"] == 1


def test_new_cards_come_from_the_log_and_the_backfill():
    rows = [row(0, state_before="new", before=0, after=1)]
    daily = history.activity(rows, 3, TODAY, introduced={"2026-09-08": 12})
    assert daily[-1]["new"] == 1        # from the log
    assert daily[0]["new"] == 12        # backfilled for a day before logging


def test_retention_only_counts_cards_that_had_been_learned():
    rows = [row(0, grade=0, state_before="review"),      # a real lapse
            row(0, grade=2, state_before="review"),
            row(0, grade=0, state_before="learning")]    # still in steps: not counted
    daily = history.activity(rows, 1, TODAY)
    assert daily[-1]["graded"] == 2 and daily[-1]["correct"] == 1
    assert history.retention(daily)[-1]["value"] == 50.0


def test_retention_is_none_before_anything_was_graded():
    daily = history.activity([], 3, TODAY)
    assert all(point["value"] is None for point in history.retention(daily))


def test_retention_rolls_over_seven_days():
    rows = [row(-8, grade=0)] + [row(-i, grade=2) for i in range(7)]
    daily = history.activity(rows, 10, TODAY)
    assert history.retention(daily, window=7)[-1]["value"] == 100.0   # the lapse aged out


# -------------------------------------------------------------------- curves

def test_learned_curve_ends_at_the_deck_truth():
    mature = card("Katze")
    mature.srs = SRS(state="review", interval_days=10)
    young = card("Hund")
    young.srs = SRS(state="review", interval_days=1)
    series = history.learned_curve([], [mature, young], 5, TODAY)
    assert series[-1]["forward"] == 1
    assert all(point["forward"] == 1 for point in series)    # flat without a log


def test_learned_curve_rewinds_a_crossing():
    mature = card("Katze")
    mature.srs = SRS(state="review", interval_days=10)
    rows = [row(0, before=1.0, after=10.0)]        # crossed the 3-day line today
    series = history.learned_curve(rows, [mature], 3, TODAY)
    assert [p["forward"] for p in series] == [0, 0, 1]


def test_learned_curve_rewinds_a_lapse():
    lapsed = card("Katze")
    lapsed.srs = SRS(state="relearning", interval_days=1.0)
    rows = [row(0, before=8.0, after=1.0)]         # fell back below the line today
    series = history.learned_curve(rows, [lapsed], 3, TODAY)
    assert [p["forward"] for p in series] == [1, 1, 0]


def test_learned_curve_keeps_the_directions_apart():
    both = card("Katze")
    both.srs = SRS(state="review", interval_days=10)
    both.srs_reverse = SRS(state="review", interval_days=5, reps=3)
    rows = [row(0, direction=REVERSE, before=1.0, after=5.0)]
    series = history.learned_curve(rows, [both], 2, TODAY)
    assert {k: series[-1][k] for k in ("date", "forward", "reverse")} == \
        {"date": "2026-09-10", "forward": 1, "reverse": 1}
    assert series[0]["reverse"] == 0 and series[0]["forward"] == 1


# ------------------------------------------------------------------ forecast

def test_forecast_buckets_by_due_date():
    now = dt.datetime.now(dt.timezone.utc)
    soon, later = card("Katze"), card("Hund")
    soon.srs = SRS(state="review", interval_days=1, due=now + dt.timedelta(days=1))
    later.srs = SRS(state="review", interval_days=3, due=now + dt.timedelta(days=3))
    days = history.forecast([soon, later], {}, 5, now)
    assert sum(d["forward"] for d in days) == 2
    assert days[1]["forward"] == 1 and days[3]["forward"] == 1


def test_overdue_cards_land_on_today():
    now = dt.datetime.now(dt.timezone.utc)
    late = card("Katze")
    late.srs = SRS(state="review", interval_days=2, due=now - dt.timedelta(days=9))
    days = history.forecast([late], {}, 5, now)
    assert days[0]["forward"] == 1


def test_forecast_skips_grammar_unless_enabled():
    now = dt.datetime.now(dt.timezone.utc)
    rule = Card.from_dict({"type": "grammar", "lemma": "Passiv", "definition": "x"})
    rule.srs = SRS(state="review", interval_days=2, due=now + dt.timedelta(days=1))
    assert sum(d["forward"] for d in history.forecast([rule], {}, 5, now)) == 0
    on = history.forecast([rule], {"grammar_enabled": True}, 5, now)
    assert sum(d["forward"] for d in on) == 1


def test_forecast_ignores_a_reverse_side_that_never_started():
    now = dt.datetime.now(dt.timezone.utc)
    c = card("Katze")
    c.srs = SRS(state="review", interval_days=5, due=now + dt.timedelta(days=2))
    c.srs_reverse = SRS(state="review", interval_days=2, due=now + dt.timedelta(days=1))
    days = history.forecast([c], {}, 5, now)
    assert sum(d["reverse"] for d in days) == 0      # reps == 0: not in play
    c.srs_reverse.reps = 2
    assert sum(d["reverse"] for d in history.forecast([c], {}, 5, now)) == 1


# ----------------------------------------------------------------- intervals

def test_interval_bands():
    cards = []
    for interval, state in [(0, "new"), (0, "learning"), (2, "review"),
                            (5, "review"), (10, "review"), (40, "review")]:
        c = card(f"W{interval}{state}")
        c.srs = SRS(state=state, interval_days=interval)
        cards.append(c)
    bands = history.intervals(cards, {})
    assert [b["count"] for b in bands] == [1, 1, 1, 1, 1, 1]
    assert [b["label"] for b in bands][:2] == ["neu", "im Lernen"]


def test_intervals_count_a_started_production_side_separately():
    c = card("Katze")
    c.srs = SRS(state="review", interval_days=10)
    c.srs_reverse = SRS(state="learning", interval_days=0, reps=1)
    bands = {b["label"]: b["count"] for b in history.intervals([c], {})}
    assert bands["1–2 Wo."] == 1 and bands["im Lernen"] == 1


# ------------------------------------------------------------------- summary

def test_summary_headlines():
    rows = [row(0), row(0, grade=0), row(-2)]
    daily = history.activity(rows, 7, TODAY)
    got = history.summary(rows, daily)
    assert got["reviews_today"] == 2 and got["reviews_week"] == 3
    assert got["retention_week"] == pytest.approx(66.7)
    assert got["logged_total"] == 3 and got["logged_days"] == 2


# ------------------------------------------------- the threshold near a deadline

def target_settings(days_ahead: int) -> dict:
    """A deadline that many days after TODAY, with the default 3-review split."""
    return {"target_date": (TODAY + dt.timedelta(days=days_ahead)).isoformat(),
            "reviews_before_target": 3.0, "min_interval_days": 0.25}


def test_threshold_follows_the_cap_when_the_deadline_closes_in():
    from flashcard.scheduler import MATURE_INTERVAL_DAYS, effective_threshold
    far = dt.datetime.combine(TODAY, dt.time(12), tzinfo=dt.timezone.utc)
    assert effective_threshold(MATURE_INTERVAL_DAYS, target_settings(30), far) == 3.0
    assert effective_threshold(MATURE_INTERVAL_DAYS, target_settings(9), far) == 3.0
    assert effective_threshold(MATURE_INTERVAL_DAYS, target_settings(6), far) == 2.0
    assert effective_threshold(MATURE_INTERVAL_DAYS, target_settings(0), far) == 3.0   # cap lifts


def test_a_capped_card_still_counts_as_learned():
    """The bug this fixes: a 2.7-day card under a 2.7-day cap is as learned as
    the schedule permits, and must not silently drop out of the count."""
    c = card("Katze")
    c.srs = SRS(state="review", interval_days=2.7)
    tight = history.learned_curve([], [c], 2, TODAY, target_settings(8))
    assert tight[-1]["forward"] == 1
    assert tight[-1]["threshold"] < 3.0
    loose = history.learned_curve([], [c], 2, TODAY, {"target_date": None})
    assert loose[-1]["forward"] == 0        # with no deadline, 3 days is the mark


def test_curve_replays_the_state_from_before_the_first_logged_review():
    c = card("Katze")
    c.srs = SRS(state="review", interval_days=10)
    rows = [row(-1, before=1.0, after=10.0)]     # crossed yesterday
    series = history.learned_curve(rows, [c], 4, TODAY, {"target_date": None})
    assert [p["forward"] for p in series] == [0, 0, 1, 1]


def test_curve_ends_on_the_deck_even_if_the_log_disagrees():
    c = card("Katze")
    c.srs = SRS(state="review", interval_days=9)          # deck says learned
    rows = [row(0, before=9.0, after=0.5, grade=0)]       # log says it lapsed
    series = history.learned_curve(rows, [c], 2, TODAY, {"target_date": None})
    assert series[-1]["forward"] == 1


def test_learning_cards_never_count_however_long_the_interval():
    c = card("Katze")
    c.srs = SRS(state="relearning", interval_days=20)
    assert history.learned_curve([], [c], 2, TODAY, {})[-1]["forward"] == 0
