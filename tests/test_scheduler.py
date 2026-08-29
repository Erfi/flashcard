import datetime as dt

import pytest

from flashcard.models import AGAIN, EASY, GOOD, HARD, Card, SRS
from flashcard.scheduler import (DEFAULT_SETTINGS, Scheduler, build_queue,
                                 humanise, projection)

NOW = dt.datetime(2026, 8, 29, 9, 0, tzinfo=dt.timezone.utc)


def make(**kw) -> Card:
    srs = kw.pop("srs", None)
    card = Card(id=kw.pop("id", "x"), lemma=kw.pop("lemma", "Katze"), **kw)
    if srs:
        card.srs = srs
    return card


def test_new_card_walks_the_learning_steps():
    s = Scheduler({"target_date": None})
    card = make()
    s.answer(card, GOOD, NOW)
    assert card.srs.state == "learning" and card.srs.step == 1
    assert card.srs.due == NOW + dt.timedelta(minutes=60)
    s.answer(card, GOOD, NOW)
    assert card.srs.step == 2
    s.answer(card, GOOD, NOW)          # off the end of the steps -> review
    assert card.srs.state == "review"
    assert card.srs.interval_days == pytest.approx(1.0)


def test_again_resets_to_first_step():
    s = Scheduler({"target_date": None})
    card = make()
    s.answer(card, GOOD, NOW)
    s.answer(card, AGAIN, NOW)
    assert card.srs.step == 0
    assert card.srs.due == NOW + dt.timedelta(minutes=10)


def test_easy_graduates_immediately():
    s = Scheduler({"target_date": None})
    card = make()
    s.answer(card, EASY, NOW)
    assert card.srs.state == "review"
    assert card.srs.interval_days == pytest.approx(2.0)


def test_review_intervals_grow_with_ease():
    s = Scheduler({"target_date": None, "fuzz": False})
    card = make(srs=SRS(state="review", ease=2.5, interval_days=4, due=NOW, reps=5))
    s.answer(card, GOOD, NOW)
    assert card.srs.interval_days == pytest.approx(10.0)
    assert card.srs.due == NOW + dt.timedelta(days=10)


def test_hard_and_easy_move_the_ease_factor():
    s = Scheduler({"target_date": None, "fuzz": False})
    hard = make(srs=SRS(state="review", ease=2.5, interval_days=10, due=NOW))
    s.answer(hard, HARD, NOW)
    assert hard.srs.ease == pytest.approx(2.35)
    assert hard.srs.interval_days == pytest.approx(12.0)

    easy = make(srs=SRS(state="review", ease=2.5, interval_days=10, due=NOW))
    s.answer(easy, EASY, NOW)
    assert easy.srs.ease == pytest.approx(2.65)
    assert easy.srs.interval_days == pytest.approx(32.5)


def test_ease_never_drops_below_the_floor():
    s = Scheduler({"target_date": None})
    card = make(srs=SRS(state="review", ease=1.35, interval_days=5, due=NOW))
    s.answer(card, AGAIN, NOW)
    assert card.srs.ease == pytest.approx(1.3)
    assert card.srs.state == "relearning"
    assert card.srs.lapses == 1


def test_lapsed_card_returns_through_relearning_steps():
    s = Scheduler({"target_date": None})
    card = make(srs=SRS(state="review", ease=2.5, interval_days=20, due=NOW))
    s.answer(card, AGAIN, NOW)
    assert card.srs.due == NOW + dt.timedelta(minutes=10)
    s.answer(card, GOOD, NOW)
    assert card.srs.state == "relearning" and card.srs.step == 1
    s.answer(card, GOOD, NOW)
    assert card.srs.state == "review"
    assert card.srs.interval_days == pytest.approx(10.0)  # 20 * lapse_multiplier


# ------------------------------------------------------------- the deadline

def test_interval_cap_follows_the_days_remaining():
    s = Scheduler({"target_date": "2026-09-18", "reviews_before_target": 3.0})
    # 20 days out -> no interval longer than ~6.7 days
    assert s.interval_cap(NOW) == pytest.approx(20 / 3)
    later = NOW + dt.timedelta(days=17)   # 3 days left
    assert s.interval_cap(later) == pytest.approx(1.0)


def test_long_interval_is_clamped_before_the_target_date():
    s = Scheduler({"target_date": "2026-09-18", "reviews_before_target": 3.0, "fuzz": False})
    card = make(srs=SRS(state="review", ease=2.5, interval_days=30, due=NOW))
    s.answer(card, EASY, NOW)
    assert card.srs.interval_days == pytest.approx(20 / 3)
    assert card.srs.due < dt.datetime(2026, 9, 18, tzinfo=dt.timezone.utc)


def test_cap_disappears_after_the_target_date():
    s = Scheduler({"target_date": "2026-09-18", "fuzz": False})
    after = dt.datetime(2026, 10, 1, tzinfo=dt.timezone.utc)
    assert s.interval_cap(after) == DEFAULT_SETTINGS["max_interval_days"]
    card = make(srs=SRS(state="review", ease=2.5, interval_days=30, due=after))
    s.answer(card, GOOD, after)
    assert card.srs.interval_days == pytest.approx(75.0)


def test_max_interval_still_applies_without_a_deadline():
    s = Scheduler({"target_date": None, "fuzz": False, "max_interval_days": 60})
    card = make(srs=SRS(state="review", ease=2.5, interval_days=50, due=NOW))
    s.answer(card, GOOD, NOW)
    assert card.srs.interval_days == pytest.approx(60.0)


# ------------------------------------------------------------------- queue

def test_queue_order_learning_then_review_then_new():
    learning = make(id="l", srs=SRS(state="learning", due=NOW - dt.timedelta(minutes=5)))
    review = make(id="r", srs=SRS(state="review", due=NOW - dt.timedelta(days=1), interval_days=3))
    new = make(id="n")
    future = make(id="f", srs=SRS(state="review", due=NOW + dt.timedelta(days=2), interval_days=2))
    queue = build_queue([future, new, review, learning], DEFAULT_SETTINGS, NOW)
    assert [c.id for c in queue] == ["l", "r", "n"]


def test_daily_new_limit_is_respected():
    cards = [make(id=f"n{i}") for i in range(10)]
    queue = build_queue(cards, {"daily_new_limit": 4}, NOW, introduced_today=1)
    assert len(queue) == 3


def test_preview_labels_are_readable():
    s = Scheduler({"target_date": "2026-09-18"})
    labels = s.preview(make(), NOW)
    assert labels["0"].endswith("Min.")
    assert set(labels) == {"0", "1", "2", "3"}


def test_projection_reports_the_horizon():
    cards = [make(id="a"), make(id="b", srs=SRS(state="review", interval_days=5, due=NOW))]
    info = projection(cards, {"target_date": "2026-09-18", "reviews_before_target": 3.0}, NOW)
    assert info["total"] == 2 and info["unseen"] == 1 and info["mature"] == 1
    assert info["days_left"] == 20


@pytest.mark.parametrize("seconds,expected", [
    (600, "10 Min."), (3600, "1 Std."), (7200, "2 Std."),
    (86400 * 3, "3 T."), (86400 * 60, "2.0 Mon."),
])
def test_humanise(seconds, expected):
    assert humanise(seconds) == expected
