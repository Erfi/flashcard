import datetime as dt

import pytest

from flashcard.models import SRS, Card
from flashcard.paths import SEED_DECK
from flashcard.store import DeckError, Store


@pytest.fixture()
def store(tmp_path):
    return Store(tmp_path / "deck.yaml")


def test_new_deck_is_created_on_disk(store):
    assert store.path.exists()
    assert store.cards == []
    assert "settings" in store.path.read_text(encoding="utf-8")


def test_round_trip_keeps_every_field(store):
    card = Card.from_dict({
        "type": "verb", "lemma": "laufen", "praeteritum": "lief",
        "perfekt": "ist gelaufen", "aux": "sein", "separable": False,
        "definition": "sich schnell zu Fuß bewegen",
        "example": "Sie läuft jeden Morgen im Park.", "tags": ["sport"],
    })
    store.add(card)
    reloaded = Store(store.path)
    got = reloaded.by_lemma("laufen")
    assert got.perfekt == "ist gelaufen"
    assert got.aux == "sein"
    assert got.tags == ["sport"]


def test_srs_state_survives_a_reload(store):
    card = store.add(Card.from_dict({"lemma": "Katze", "type": "noun", "article": "die"}))
    card.srs = SRS(state="review", ease=2.3, interval_days=4.5,
                   due=dt.datetime(2026, 9, 1, tzinfo=dt.timezone.utc), reps=3, lapses=1)
    store.save()
    got = Store(store.path).by_id(card.id)
    assert got.srs.state == "review"
    assert got.srs.interval_days == 4.5
    assert got.srs.due.day == 1


def test_ids_stay_unique(store):
    a = store.add(Card.from_dict({"lemma": "Bank", "type": "noun", "article": "die"}))
    b = store.add(Card.from_dict({"lemma": "Bank", "type": "noun", "article": "die"}))
    assert a.id == "bank" and b.id == "bank-2"


def test_umlauts_slug_readably(store):
    card = store.add(Card.from_dict({"lemma": "Möglichkeit", "type": "noun", "article": "die"}))
    assert card.id == "moeglichkeit"


def test_hand_edits_are_picked_up(store):
    store.add(Card.from_dict({"lemma": "Hund", "type": "noun", "article": "der"}))
    text = store.path.read_text(encoding="utf-8").replace("article: der", "article: das")
    store.path.write_text(text, encoding="utf-8")
    import os, time
    os.utime(store.path, (time.time() + 1, time.time() + 1))
    store.reload_if_changed()
    assert store.by_lemma("Hund").article == "das"


def test_update_preserves_progress(store):
    card = store.add(Card.from_dict({"lemma": "Katze", "type": "noun", "article": "die"}))
    card.srs = SRS(state="review", reps=7, interval_days=3)
    store.save()
    updated = store.update(card.id, {"example": "Die Katze schläft."})
    assert updated.example == "Die Katze schläft."
    assert updated.srs.reps == 7


def test_broken_yaml_raises_a_clear_error(tmp_path):
    path = tmp_path / "deck.yaml"
    path.write_text("cards: [oops\n", encoding="utf-8")
    with pytest.raises(DeckError):
        Store(path)


def test_import_merge_skips_duplicates(store):
    store.add(Card.from_dict({"lemma": "Katze", "type": "noun", "article": "die"}))
    payload = "cards:\n  - {lemma: Katze, type: noun, article: die}\n  - {lemma: Hund, type: noun, article: der}\n"
    result = store.import_cards(payload, mode="merge")
    assert result == {"added": 1, "skipped": 1, "total": 2}


def test_import_replace_wipes_first(store):
    store.add(Card.from_dict({"lemma": "Katze", "type": "noun", "article": "die"}))
    result = store.import_cards("cards:\n  - {lemma: Hund, type: noun, article: der}\n", mode="replace")
    assert result["total"] == 1 and store.by_lemma("Katze") is None


def test_backup_written_before_overwrite(store):
    store.add(Card.from_dict({"lemma": "Katze", "type": "noun"}))
    store.add(Card.from_dict({"lemma": "Hund", "type": "noun"}))
    assert store.path.with_suffix(".yaml.bak").exists()


def test_known_lemmas_put_the_best_known_first(store):
    weak = store.add(Card.from_dict({"lemma": "Neu", "type": "noun"}))
    strong = store.add(Card.from_dict({"lemma": "Alt", "type": "noun", "article": "der"}))
    strong.srs = SRS(state="review", interval_days=12, reps=9)
    store.save()
    assert store.known_lemmas()[0] == "der Alt"


def test_seed_deck_imports_and_is_well_formed(store):
    result = store.import_cards(SEED_DECK.read_text(encoding="utf-8"))
    assert result["added"] > 60
    nouns = [c for c in store.cards if c.type == "noun"]
    verbs = [c for c in store.cards if c.type == "verb"]
    assert all(c.article in ("der", "die", "das") for c in nouns)
    assert all(c.praeteritum and c.perfekt for c in verbs)
    assert all(c.definition and c.example for c in store.cards)
    assert all(c.perfekt.startswith(("hat ", "ist ")) for c in verbs)


def test_introduced_counter(store):
    assert store.introduced_today() == 0
    store.note_introduced()
    store.note_introduced()
    assert store.introduced_today() == 2
