"""Duplicate detection when adding, renaming and importing cards."""
import pytest
from fastapi.testclient import TestClient

from flashcard import server
from flashcard.models import Card
from flashcard.store import Store, normalise_lemma, similarity


@pytest.mark.parametrize("a,b", [
    ("Katze", "die Katze"), ("Katze", "katze"), ("Katze", "  KATZE "),
    ("Vertrag", "der Vertrag"), ("Möglichkeit", "die Möglichkeit"),
])
def test_the_same_word_normalises_the_same_way(a, b):
    assert normalise_lemma(a) == normalise_lemma(b)


@pytest.mark.parametrize("a,b", [
    ("Katze", "Hund"), ("Vertrag", "vertragen"), ("schenken", "schmecken"),
    ("Kleid", "klein"), ("aufmachen", "zumachen"),
])
def test_different_words_stay_different(a, b):
    assert normalise_lemma(a) != normalise_lemma(b)


def test_word_families_are_flagged_as_similar_not_identical():
    assert similarity("erinnern", "erinnerung") >= 0.9
    assert similarity("arbeit", "arbeiten") >= 0.9
    assert normalise_lemma("Erinnerung") != normalise_lemma("erinnern")


def test_unrelated_lookalikes_score_low():
    assert similarity("schenken", "schmecken") < 0.9
    assert similarity("bewegung", "bewerbung") < 0.9


@pytest.mark.parametrize("typed,lemma,article", [
    ("die Katze", "Katze", "die"), ("der Vertrag", "Vertrag", "der"),
    ("das Haus", "Haus", "das"), ("Katze", "Katze", ""),
])
def test_an_article_typed_into_the_lemma_is_moved_where_it_belongs(typed, lemma, article):
    card = Card.from_dict({"type": "noun", "lemma": typed})
    assert card.lemma == lemma and card.article == article


def test_a_phrase_keeps_its_leading_article():
    card = Card.from_dict({"type": "phrase", "lemma": "die Nase voll haben"})
    assert card.lemma == "die Nase voll haben"


def test_find_duplicate_ignores_article_and_case(tmp_path):
    store = Store(tmp_path / "deck.yaml")
    store.add(Card.from_dict({"type": "noun", "article": "die", "lemma": "Katze"}))
    assert store.find_duplicate("die Katze") is not None
    assert store.find_duplicate("KATZE") is not None
    assert store.find_duplicate("Katzen") is None      # a form, caught after generation


def test_import_merge_skips_a_differently_written_duplicate(tmp_path):
    store = Store(tmp_path / "deck.yaml")
    store.add(Card.from_dict({"type": "noun", "article": "die", "lemma": "Katze"}))
    result = store.import_cards(
        "cards:\n  - {lemma: die Katze, type: noun, article: die}\n"
        "  - {lemma: Hund, type: noun, article: der}\n")
    assert result == {"added": 1, "skipped": 1, "total": 2}


# --------------------------------------------------------------------- API

class InflectedGenerator:
    """Stands in for Claude: normalises whatever was typed to the lemma."""
    available = True
    LEMMAS = {"läuft": "laufen", "gelaufen": "laufen", "lief": "laufen",
              "die katze": "Katze", "katzen": "Katze"}

    def generate_card(self, query, known=None, topics=None, hint_type=""):
        lemma = self.LEMMAS.get(query.strip().lower(), query.strip())
        if lemma == "laufen":
            return {"type": "verb", "lemma": "laufen", "praesens_3sg": "läuft",
                    "praeteritum": "lief", "perfekt": "ist gelaufen", "aux": "sein",
                    "definition": "Sich schnell zu Fuß bewegen.",
                    "example": "Sie läuft jeden Morgen im Park."}
        return {"type": "noun", "lemma": lemma, "article": "die", "plural": "die Katzen",
                "definition": "Ein kleines Haustier.", "example": "Die Katze schläft."}

    def generate_sentence(self, *a, **kw):
        return "Ein neuer Satz."

    def generate_examples(self, *a, **kw):
        return ["Eins.", "Zwei.", "Drei."]


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "ClaudeGenerator", InflectedGenerator)
    return TestClient(server.create_app(tmp_path / "deck.yaml"))


def test_typing_an_inflected_form_finds_the_existing_card(client):
    client.post("/api/command", json={"text": "add laufen"})
    res = client.post("/api/command", json={"text": "add gelaufen"}).json()
    assert res["action"] == "duplicate"
    assert res["card"]["lemma"] == "laufen"
    assert "gelaufen" in res["message"]
    assert client.get("/api/cards").json()["total"] == 1


def test_typing_the_article_finds_the_existing_card(client):
    client.post("/api/command", json={"text": "add katze"})
    res = client.post("/api/command", json={"text": "add die Katze"}).json()
    assert res["action"] == "duplicate"
    assert client.get("/api/cards").json()["total"] == 1


def test_a_new_word_still_reports_similar_cards(client):
    client.post("/api/cards", json={"type": "verb", "lemma": "erinnern",
                                    "definition": "x", "example": "y"})
    res = client.post("/api/cards", json={"type": "noun", "article": "die",
                                          "lemma": "Erinnerung", "definition": "x",
                                          "example": "y"}).json()
    assert [c["lemma"] for c in res["similar"]] == ["erinnern"]
    assert client.get("/api/cards").json()["total"] == 2    # both are kept


def test_manual_creation_refuses_an_exact_duplicate(client):
    client.post("/api/cards", json={"type": "noun", "article": "die", "lemma": "Katze"})
    clash = client.post("/api/cards", json={"type": "noun", "article": "die",
                                            "lemma": "die Katze"})
    assert clash.status_code == 409
    assert client.post("/api/cards", json={"type": "noun", "article": "die",
                                           "lemma": "die Katze",
                                           "allow_duplicate": True}).status_code == 200


def test_renaming_a_card_onto_another_is_refused(client):
    client.post("/api/cards", json={"type": "noun", "article": "die", "lemma": "Katze"})
    other = client.post("/api/cards", json={"type": "noun", "article": "der",
                                            "lemma": "Hund"}).json()["card"]
    clash = client.patch(f"/api/cards/{other['id']}", json={"lemma": "Katze"})
    assert clash.status_code == 409
    ok = client.patch(f"/api/cards/{other['id']}", json={"lemma": "Hund", "notes": "x"})
    assert ok.status_code == 200                      # renaming to itself is fine


def test_duplicates_report(client):
    client.post("/api/cards", json={"type": "noun", "article": "die", "lemma": "Katze"})
    client.post("/api/cards", json={"type": "noun", "article": "die", "lemma": "die Katze",
                                    "allow_duplicate": True})
    client.post("/api/cards", json={"type": "verb", "lemma": "erinnern"})
    client.post("/api/cards", json={"type": "noun", "article": "die", "lemma": "Erinnerung"})
    report = client.get("/api/duplicates").json()
    assert len(report["exact"]) == 1
    assert {c["id"] for c in report["exact"][0]} == {"katze", "katze-2"}
    assert any({"erinnern", "Erinnerung"} == {c["lemma"] for c in pair["cards"]}
               for pair in report["near"])
