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
    """One logged answer. grade 2 (Gut) counts as a success by default."""
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


def test_new_cards_are_split_by_direction():
    """One daily allowance feeds both directions, so a day can look busy while
    one of them got nothing — which is exactly what happened on 10 September."""
    rows = [row(0, state_before="new", before=0, after=1, direction=REVERSE)
            for _ in range(5)]
    rows.append(row(0, state_before="new", before=0, after=1))
    daily = history.activity(rows, 2, TODAY)
    assert daily[-1]["new"] == 6
    assert daily[-1]["new_forward"] == 1
    assert daily[-1]["new_reverse"] == 5
    assert daily[-1]["new_forward"] + daily[-1]["new_reverse"] == daily[-1]["new"]


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

def learned(card_list, rows, days=4, settings=None):
    return history.learned_curve(rows, card_list, days, TODAY, settings or {})


def mature_card(lemma="Katze", successes=3, state="review", interval=4.0, reps=None):
    c = card(lemma)
    c.srs = SRS(state=state, interval_days=interval, successes=successes,
                reps=reps if reps is not None else successes)
    return c


def test_three_correct_recalls_make_a_card_learned():
    assert learned([mature_card(successes=3)], [])[-1]["forward"] == 1
    assert learned([mature_card(successes=2)], [])[-1]["forward"] == 0


def test_a_card_still_in_learning_never_counts():
    assert learned([mature_card(successes=9, state="learning")], [])[-1]["forward"] == 0
    assert learned([mature_card(successes=9, state="relearning")], [])[-1]["forward"] == 0


def test_interval_length_no_longer_decides():
    """The point of the change: a short interval under a tight cap is still
    a learned card, and a long interval without recalls is not."""
    short = mature_card(successes=4, interval=0.5)
    long_but_untested = mature_card("Hund", successes=1, interval=40)
    series = learned([short, long_but_untested], [])
    assert series[-1]["forward"] == 1


def test_the_curve_does_not_move_when_the_deadline_closes_in():
    """This is the bug that prompted the change: with a fixed card set, the
    count must be identical whatever the interval cap happens to be."""
    cards = [mature_card(f"W{i}", successes=3, interval=2.5) for i in range(20)]
    far = learned(cards, [], settings={"target_date": "2027-01-01"})
    near = learned(cards, [], settings={"target_date": (TODAY + dt.timedelta(days=4)).isoformat()})
    none = learned(cards, [], settings={"target_date": None})
    assert far[-1]["forward"] == near[-1]["forward"] == none[-1]["forward"] == 20


def test_curve_counts_recalls_as_they_are_logged():
    c = mature_card(successes=3)
    rows = [row(-2), row(-1), row(0)]          # the three that made it learned
    series = learned([c], rows, days=4)
    assert [p["forward"] for p in series] == [0, 0, 0, 1]


def test_a_failed_answer_does_not_count_towards_the_three():
    c = mature_card(successes=2, reps=3)
    rows = [row(-2), row(-1, grade=0), row(0)]
    assert learned([c], rows, days=4)[-1]["forward"] == 0


def test_hard_counts_as_a_recall():
    """Schwer is a pass in SM-2 — the card stays in review — so a word you
    always get right but always find hard must be able to count as learned."""
    c = mature_card(successes=3, reps=3)
    rows = [row(-2, grade=1), row(-1, grade=1), row(0, grade=1)]
    assert learned([c], rows, days=4)[-1]["forward"] == 1


def test_forgetting_a_card_sends_the_run_back_to_zero():
    c = card("Katze")
    c.srs = SRS(state="review", interval_days=2, successes=1, reps=5)
    rows = [row(-4), row(-3), row(-2),            # learned by day -2
            row(-1, grade=0),                     # forgotten
            row(0)]                               # one recall since
    series = learned([c], rows, days=5)
    assert [p["forward"] for p in series] == [0, 0, 1, 0, 0]


def test_curve_keeps_history_earned_before_the_window():
    c = mature_card(successes=5)
    rows = [row(0)]                            # only one recall inside the window
    series = learned([c], rows, days=3)
    assert [p["forward"] for p in series] == [1, 1, 1]   # already learned beforehand


def test_curve_ends_on_the_deck_even_if_the_log_disagrees():
    c = mature_card(successes=4)
    rows = [row(0, grade=0, state_before="review")]      # log says it lapsed today
    assert learned([c], rows, days=2)[-1]["forward"] == 1


def test_learned_curve_keeps_the_directions_apart():
    both = card("Katze")
    both.srs = SRS(state="review", interval_days=10, successes=4, reps=4)
    both.srs_reverse = SRS(state="review", interval_days=5, successes=3, reps=3)
    rows = [row(0, direction=REVERSE)]
    series = learned([both], rows, days=2)
    assert series[-1] == {"date": "2026-09-10", "forward": 1, "reverse": 1}
    assert series[0]["reverse"] == 0 and series[0]["forward"] == 1


# ------------------------------------------------------------------ backfill

def test_backfill_counts_the_run_since_the_last_failure():
    c = card("Katze")
    c.srs = SRS(state="review", reps=4, successes=0, lapses=1)
    rows = [row(-3), row(-2), row(-1, grade=0), row(0)]   # failed, then one recall
    assert history.backfill_successes([c], rows) == 1
    assert c.srs.successes == 1


def test_backfill_counts_hard_as_a_recall():
    c = card("Katze")
    c.srs = SRS(state="review", reps=3, successes=0)
    rows = [row(-2, grade=1), row(-1, grade=1), row(0, grade=2)]
    history.backfill_successes([c], rows)
    assert c.srs.successes == 3


def test_backfill_adds_answers_that_predate_the_log():
    c = card("Katze")
    c.srs = SRS(state="review", reps=6, successes=0, lapses=0)   # 6 answers, 2 logged
    rows = [row(-1), row(0)]
    history.backfill_successes([c], rows)
    assert c.srs.successes == 6


def test_backfill_leaves_a_counted_card_alone_unless_forced():
    c = card("Katze")
    c.srs = SRS(state="review", reps=9, successes=7)
    assert history.backfill_successes([c], []) == 0
    assert c.srs.successes == 7
    assert history.backfill_successes([c], [], force=True) == 1
    assert c.srs.successes == 9        # recomputed: 9 answers, none logged as failures


def test_backfill_ignores_cards_that_were_never_reviewed():
    c = card("Katze")
    assert history.backfill_successes([c], []) == 0
    assert c.srs.successes == 0


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




def test_a_card_whose_whole_history_is_in_the_window_starts_at_zero():
    c = card("Katze")
    c.srs = SRS(state="review", successes=3, reps=3)
    rows = [row(-2), row(-1), row(0)]
    assert [p["forward"] for p in learned([c], rows, days=4)] == [0, 0, 0, 1]


def test_a_card_learned_before_the_window_shows_as_learned_before_it_lapsed():
    c = card("Katze")
    c.srs = SRS(state="relearning", successes=0, reps=40)     # a long history
    rows = [row(0, grade=0)]                                  # today it failed
    series = learned([c], rows, days=3)
    assert [p["forward"] for p in series] == [1, 1, 0]
