"""End-to-end checks against the HTTP layer, with generation stubbed out."""
import pytest
from fastapi.testclient import TestClient

from flashcard import server
from flashcard.generator import GeneratorError


class FakeGenerator:
    available = True

    def generate_card(self, query, known=None, topics=None, hint_type=""):
        return {"type": "noun", "lemma": "Katze", "article": "die", "plural": "die Katzen",
                "definition": "Ein kleines Haustier.", "example": "Die Katze schläft.",
                "tags": ["tier"], "level": "B1"}

    def generate_sentence(self, summary, known=None, topics=None, avoid=""):
        return "Die Katze sitzt auf dem Vertrag."

    def generate_examples(self, summary, known=None, topics=None, avoid="", count=3):
        return ["Der Antrag wird geprüft.", "Die Rechnung wurde bezahlt.",
                "Das Formular ist ausgefüllt worden."]


class BrokenGenerator(FakeGenerator):
    available = False

    def generate_card(self, *a, **kw):
        raise GeneratorError("Kein ANTHROPIC_API_KEY gesetzt")


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "ClaudeGenerator", FakeGenerator)
    return TestClient(server.create_app(tmp_path / "deck.yaml"))


def test_add_via_command_creates_a_full_card(client):
    res = client.post("/api/command", json={"text": "add katze"}).json()
    assert res["action"] == "added"
    assert res["card"]["article"] == "die"
    assert res["card"]["color_key"] == "die"
    assert res["card"]["headword"] == "die Katze"


def test_duplicates_are_reported_not_added(client):
    client.post("/api/command", json={"text": "add katze"})
    res = client.post("/api/command", json={"text": "add Katze"}).json()
    assert res["action"] == "duplicate"
    assert client.get("/api/cards").json()["total"] == 1


def test_review_cycle_updates_scheduling(client):
    client.post("/api/command", json={"text": "add katze"})
    queue = client.get("/api/queue").json()
    card = queue["cards"][0]
    assert card["preview"]["2"]
    res = client.post("/api/answer", json={"id": card["id"], "grade": 2}).json()
    assert res["card"]["srs"]["state"] == "learning"
    assert res["card"]["srs"]["reps"] == 1


def test_edit_then_regenerate_sentence(client):
    card = client.post("/api/command", json={"text": "add katze"}).json()["card"]
    patched = client.patch(f"/api/cards/{card['id']}", json={"example": "Von Hand geschrieben."}).json()
    assert patched["card"]["example"] == "Von Hand geschrieben."
    fresh = client.post(f"/api/cards/{card['id']}/sentence").json()
    assert fresh["card"]["example"] == "Die Katze sitzt auf dem Vertrag."


def test_settings_command_changes_the_interval_cap(client):
    client.post("/api/command", json={"text": "set target 2099-01-01"})
    state = client.get("/api/state").json()
    assert state["settings"]["target_date"] == "2099-01-01"
    client.post("/api/command", json={"text": "set new 5"})
    assert client.get("/api/state").json()["settings"]["daily_new_limit"] == 5


def test_export_and_import_round_trip(client):
    client.post("/api/command", json={"text": "add katze"})
    text = client.get("/api/export").text
    res = client.post("/api/import", json={"yaml": text, "mode": "merge"}).json()
    assert res["skipped"] == 1 and res["added"] == 0


def test_delete_and_search(client):
    client.post("/api/command", json={"text": "add katze"})
    found = client.post("/api/command", json={"text": "find Haustier"}).json()
    assert len(found["cards"]) == 1
    deleted = client.post("/api/command", json={"text": "del katze"}).json()
    assert deleted["action"] == "deleted"
    assert client.get("/api/cards").json()["total"] == 0


def test_missing_api_key_still_creates_an_editable_card(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "ClaudeGenerator", BrokenGenerator)
    client = TestClient(server.create_app(tmp_path / "deck.yaml"))
    res = client.post("/api/command", json={"text": "add Hund"}).json()
    assert res["action"] == "added"
    assert res["warning"]
    assert res["card"]["lemma"] == "Hund" and res["card"]["type"] == "noun"


def test_unknown_card_returns_404(client):
    assert client.patch("/api/cards/nope", json={"lemma": "x"}).status_code == 404


def test_grammar_card_rerolls_all_three_examples(client):
    created = client.post("/api/cards", json={
        "type": "grammar", "lemma": "Passiv Präsens",
        "definition": "werden plus Partizip II am Satzende.",
        "examples": ["Alter Satz."],
    }).json()["card"]
    assert created["color_key"] == "grammar"
    fresh = client.post(f"/api/cards/{created['id']}/sentence").json()["card"]
    assert len(fresh["examples"]) == 3
    assert fresh["examples"][0] == "Der Antrag wird geprüft."


def test_editing_examples_by_hand_is_kept(client):
    created = client.post("/api/cards", json={
        "type": "grammar", "lemma": "Genitiv", "definition": "Besitz und Zugehörigkeit.",
        "examples": ["Eins."],
    }).json()["card"]
    patched = client.patch(f"/api/cards/{created['id']}",
                           json={"examples": ["Neu eins.", "Neu zwei."]}).json()["card"]
    assert patched["examples"] == ["Neu eins.", "Neu zwei."]
