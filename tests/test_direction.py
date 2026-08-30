"""Both study directions: recognition (forward) and production (reverse)."""
import datetime as dt
import random

import pytest

from flashcard.models import (AGAIN, EASY, FORWARD, GOOD, REVERSE, SRS, Card)
from flashcard.scheduler import (DEFAULT_SETTINGS, Scheduler, build_queue,
                                 projection, reverse_unlocked)

NOW = dt.datetime(2026, 8, 29, 9, 0, tzinfo=dt.timezone.utc)


def vocab(**kw) -> Card:
    base = {"type": "noun", "article": "die", "lemma": "Katze", "plural": "die Katzen",
            "definition": "Ein kleines Haustier.", "example": "Die Katze schläft."}
    base.update(kw)
    return Card.from_dict(base)


def mature(card: Card, interval: float = 4.0) -> Card:
    card.srs = SRS(state="review", interval_days=interval, ease=2.5,
                   due=NOW + dt.timedelta(days=interval), reps=4)
    return card


# ------------------------------------------------------------------- unlock

def test_reverse_is_locked_while_the_word_is_still_new():
    assert not reverse_unlocked(vocab())


def test_reverse_is_locked_below_the_unlock_interval():
    card = mature(vocab(), interval=1.0)
    assert not reverse_unlocked(card, 3.0)


def test_reverse_unlocks_once_recognition_is_mature():
    assert reverse_unlocked(mature(vocab(), interval=3.0), 3.0)


def test_reverse_stays_unlocked_after_it_has_started():
    card = vocab()
    card.srs_reverse = SRS(state="learning", reps=1, due=NOW)
    assert reverse_unlocked(card, 3.0)     # even though the forward card lapsed


def test_grammar_cards_have_no_reverse_direction():
    card = Card.from_dict({"type": "grammar", "lemma": "Passiv",
                           "definition": "werden plus Partizip II.",
                           "examples": ["Der Antrag wird geprüft."]})
    assert not card.supports_reverse
    assert not reverse_unlocked(mature(card))


# -------------------------------------------------------------- scheduling

def test_the_two_directions_are_scheduled_independently():
    card = mature(vocab())
    sched = Scheduler({"target_date": None, "fuzz": False})
    sched.answer(card, AGAIN, NOW, direction=REVERSE)
    assert card.srs_reverse.state == "learning"
    assert card.srs_reverse.reps == 1
    # the forward direction is untouched
    assert card.srs.state == "review"
    assert card.srs.reps == 4
    assert card.srs.interval_days == 4.0


def test_forward_grading_does_not_move_the_reverse_card():
    card = mature(vocab())
    card.srs_reverse = SRS(state="review", interval_days=2, ease=2.5, due=NOW)
    Scheduler({"target_date": None, "fuzz": False}).answer(card, GOOD, NOW)
    assert card.srs_reverse.interval_days == 2
    assert card.srs.interval_days == 10.0


def test_preview_reads_the_direction_it_is_asked_about():
    card = mature(vocab(), interval=8)
    sched = Scheduler({"target_date": None, "fuzz": False})
    assert sched.preview(card, NOW, FORWARD)["2"] != sched.preview(card, NOW, REVERSE)["2"]


def test_reverse_survives_a_yaml_round_trip(tmp_path):
    from flashcard.store import Store
    store = Store(tmp_path / "deck.yaml")
    card = store.add(mature(vocab()))
    Scheduler().answer(card, GOOD, NOW, direction=REVERSE)
    store.save()
    got = Store(store.path).by_lemma("Katze")
    assert got.srs_reverse.reps == 1
    assert got.srs_reverse.state == "learning"
    assert got.srs.reps == 4


def test_pristine_reverse_state_is_not_written_to_the_file(tmp_path):
    from flashcard.store import Store
    store = Store(tmp_path / "deck.yaml")
    store.add(vocab())
    assert "srs_reverse" not in store.path.read_text(encoding="utf-8")


# ------------------------------------------------------------------- queue

def test_queue_offers_both_directions_once_unlocked():
    card = mature(vocab())
    card.srs.due = NOW - dt.timedelta(days=1)          # forward is due as well
    items = build_queue([card], DEFAULT_SETTINGS, NOW)
    assert {d for _, d in items} == {FORWARD, REVERSE}


def test_queue_hides_the_reverse_direction_when_switched_off():
    card = mature(vocab())
    items = build_queue([card], {**DEFAULT_SETTINGS, "reverse_enabled": False}, NOW)
    assert all(d == FORWARD for _, d in items)


def test_locked_cards_contribute_only_the_forward_direction():
    cards = [vocab(lemma=f"Wort{i}") for i in range(5)]
    items = build_queue(cards, DEFAULT_SETTINGS, NOW)
    assert len(items) == 5
    assert all(d == FORWARD for _, d in items)


def test_projection_counts_the_reverse_backlog():
    cards = [mature(vocab(lemma="Katze")), vocab(lemma="Hund")]
    info = projection(cards, DEFAULT_SETTINGS, NOW)
    assert info["reverse_possible"] == 2      # both are vocabulary
    assert info["reverse_open"] == 1          # only one is mature
    assert info["reverse_started"] == 0


# ----------------------------------------------------------------- shuffle

def test_shuffle_reorders_new_cards():
    cards = [vocab(lemma=f"Wort{i:02d}") for i in range(30)]
    a = [c.lemma for c, _ in build_queue(cards, DEFAULT_SETTINGS, NOW, rng=random.Random(1))]
    b = [c.lemma for c, _ in build_queue(cards, DEFAULT_SETTINGS, NOW, rng=random.Random(2))]
    ordered = sorted(c.lemma for c in cards)
    assert a != ordered and b != ordered
    assert a != b
    assert sorted(a) == ordered            # nothing lost or duplicated


def test_shuffle_off_keeps_creation_order():
    cards = [vocab(lemma=f"Wort{i:02d}") for i in range(10)]
    queue = build_queue(cards, {**DEFAULT_SETTINGS, "shuffle": False}, NOW)
    assert [c.lemma for c, _ in queue] == [c.lemma for c in cards]


def test_shuffle_keeps_the_buckets_in_order():
    learning = vocab(lemma="Lernen")
    learning.srs = SRS(state="learning", due=NOW - dt.timedelta(minutes=1))
    due = vocab(lemma="Faellig")
    due.srs = SRS(state="review", interval_days=1, due=NOW - dt.timedelta(hours=2))
    fresh = [vocab(lemma=f"Neu{i}") for i in range(5)]
    queue = build_queue([*fresh, due, learning], DEFAULT_SETTINGS, NOW, rng=random.Random(3))
    assert queue[0][0].lemma == "Lernen"
    assert queue[1][0].lemma == "Faellig"
    assert {c.lemma for c, _ in queue[2:]} == {c.lemma for c in fresh}


def test_more_overdue_reviews_still_come_first():
    old = vocab(lemma="Alt")
    old.srs = SRS(state="review", interval_days=2, due=NOW - dt.timedelta(days=6))
    recent = [vocab(lemma=f"Neu{i}") for i in range(4)]
    for card in recent:
        card.srs = SRS(state="review", interval_days=2, due=NOW - dt.timedelta(hours=1))
    for seed in range(5):
        queue = build_queue([*recent, old], DEFAULT_SETTINGS, NOW, rng=random.Random(seed))
        assert queue[0][0].lemma == "Alt"


def test_unlocked_production_outranks_brand_new_words():
    """A big backlog of new vocabulary must not starve the production direction."""
    known = mature(vocab(lemma="Katze"))
    backlog = [vocab(lemma=f"Neu{i:03d}") for i in range(300)]
    queue = build_queue([*backlog, known], {**DEFAULT_SETTINGS, "daily_new_limit": 40},
                        NOW, rng=random.Random(0))
    assert len(queue) == 40
    assert queue[0] == (known, REVERSE)
    assert sum(1 for _, d in queue if d == REVERSE) == 1


def test_production_still_respects_the_daily_limit():
    known = [mature(vocab(lemma=f"Wort{i}")) for i in range(50)]
    queue = build_queue(known, {**DEFAULT_SETTINGS, "daily_new_limit": 10}, NOW,
                        rng=random.Random(0))
    assert len(queue) == 10


# ------------------------------------------------------------------ grammar

def grammar_card(lemma: str = "Passiv") -> Card:
    return Card.from_dict({"type": "grammar", "lemma": lemma,
                           "definition": "werden plus Partizip II am Satzende.",
                           "examples": ["Der Antrag wird geprüft."]})


def test_grammar_cards_stay_out_of_the_queue_by_default():
    queue = build_queue([grammar_card(), vocab()], DEFAULT_SETTINGS, NOW)
    assert [c.lemma for c, _ in queue] == ["Katze"]


def test_grammar_cards_join_the_queue_when_switched_on():
    queue = build_queue([grammar_card(), vocab()],
                        {**DEFAULT_SETTINGS, "grammar_enabled": True}, NOW)
    assert {c.lemma for c, _ in queue} == {"Passiv", "Katze"}


def test_hidden_grammar_does_not_use_up_the_daily_new_limit():
    cards = [grammar_card(f"Regel{i}") for i in range(30)] + [vocab(lemma=f"Wort{i}") for i in range(10)]
    queue = build_queue(cards, {**DEFAULT_SETTINGS, "daily_new_limit": 10}, NOW,
                        rng=random.Random(0))
    assert len(queue) == 10
    assert all(c.type != "grammar" for c, _ in queue)


def test_projection_separates_the_deck_from_the_rotation():
    cards = [grammar_card(f"Regel{i}") for i in range(5)] + [vocab(lemma=f"Wort{i}") for i in range(7)]
    info = projection(cards, DEFAULT_SETTINGS, NOW)
    assert info["total"] == 12
    assert info["in_rotation"] == 7
    assert info["grammar_total"] == 5
    assert info["grammar_enabled"] is False
    assert info["unseen"] == 7        # hidden grammar is not a backlog

    on = projection(cards, {**DEFAULT_SETTINGS, "grammar_enabled": True}, NOW)
    assert on["in_rotation"] == 12 and on["unseen"] == 12
