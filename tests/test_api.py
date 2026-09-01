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


def _mature(client, card_id):
    """Push a card to a review interval so its production direction unlocks."""
    for grade in (3, 3):          # easy twice: graduates, then a real interval
        client.post("/api/answer", json={"id": card_id, "grade": grade})
    return client.get("/api/cards").json()["cards"]


def test_queue_items_carry_a_direction(client):
    client.post("/api/command", json={"text": "add katze"})
    item = client.get("/api/queue").json()["cards"][0]
    assert item["direction"] == "forward"
    assert item["supports_reverse"] is True


def test_production_direction_appears_once_recognition_matures(client):
    card = client.post("/api/command", json={"text": "add katze"}).json()["card"]
    assert all(i["direction"] == "forward" for i in client.get("/api/queue").json()["cards"])
    _mature(client, card["id"])
    directions = [i["direction"] for i in client.get("/api/queue").json()["cards"]]
    assert "reverse" in directions


def test_reverse_item_hides_the_word_in_the_example(client):
    card = client.post("/api/command", json={"text": "add katze"}).json()["card"]
    _mature(client, card["id"])
    reverse = next(i for i in client.get("/api/queue").json()["cards"]
                   if i["direction"] == "reverse")
    assert "Katze" not in reverse["example_masked"]
    assert "____" in reverse["example_masked"]
    assert reverse["definition"]                     # the prompt is the definition


def test_grading_the_reverse_direction_leaves_the_forward_card_alone(client):
    card = client.post("/api/command", json={"text": "add katze"}).json()["card"]
    _mature(client, card["id"])
    before = client.get("/api/cards").json()["cards"][0]["srs"]
    after = client.post("/api/answer",
                        json={"id": card["id"], "grade": 0, "direction": "reverse"}).json()["card"]
    assert after["srs"] == before
    assert after["srs_reverse"]["reps"] == 1


def test_reverse_can_be_switched_off(client):
    card = client.post("/api/command", json={"text": "add katze"}).json()["card"]
    _mature(client, card["id"])
    client.post("/api/command", json={"text": "set reverse off"})
    assert all(i["direction"] == "forward" for i in client.get("/api/queue").json()["cards"])


def test_reset_clears_both_directions(client):
    card = client.post("/api/command", json={"text": "add katze"}).json()["card"]
    _mature(client, card["id"])
    client.post("/api/answer", json={"id": card["id"], "grade": 2, "direction": "reverse"})
    reset = client.post(f"/api/cards/{card['id']}/reset").json()["card"]
    assert reset["srs"]["state"] == "new" and reset["srs_reverse"]["state"] == "new"


def test_state_reports_the_production_backlog(client):
    card = client.post("/api/command", json={"text": "add katze"}).json()["card"]
    _mature(client, card["id"])
    state = client.get("/api/state").json()
    assert state["counts"]["reverse"] == 1
    assert state["projection"]["reverse_open"] == 1


def test_search_puts_the_word_itself_first(client):
    client.post("/api/cards", json={
        "type": "grammar", "lemma": "Wechselpräpositionen",
        "definition": "Wohin mit Akkusativ, wo mit Dativ.",
        "examples": ["Die Katze springt auf den Tisch."]})
    client.post("/api/cards", json={
        "type": "noun", "article": "die", "lemma": "Katze", "plural": "die Katzen",
        "definition": "Ein kleines Haustier.", "example": "Die Katze schläft."})
    client.post("/api/cards", json={
        "type": "noun", "article": "das", "lemma": "Katzenfutter", "plural": "",
        "definition": "Essen für ein bestimmtes Haustier.", "example": "Das Futter ist alle."})
    hits = client.get("/api/cards?q=katze").json()["cards"]
    assert [c["lemma"] for c in hits][:2] == ["Katze", "Katzenfutter"]
    assert "Wechselpräpositionen" in [c["lemma"] for c in hits]


def test_search_without_a_query_keeps_the_due_order(client):
    client.post("/api/command", json={"text": "add katze"})
    hits = client.get("/api/cards").json()["cards"]
    assert len(hits) == 1


def test_grammar_is_hidden_from_the_queue_until_switched_on(client):
    client.post("/api/cards", json={
        "type": "grammar", "lemma": "Passiv", "definition": "werden plus Partizip II.",
        "examples": ["Der Antrag wird geprüft."]})
    client.post("/api/command", json={"text": "add katze"})

    queue = client.get("/api/queue").json()["cards"]
    assert [c["lemma"] for c in queue] == ["Katze"]
    state = client.get("/api/state").json()
    assert state["settings"]["grammar_enabled"] is False
    assert state["projection"]["grammar_total"] == 1
    assert state["projection"]["in_rotation"] == 1

    client.post("/api/command", json={"text": "set grammar on"})
    assert len(client.get("/api/queue").json()["cards"]) == 2


def test_hidden_grammar_cards_are_still_browsable_and_editable(client):
    created = client.post("/api/cards", json={
        "type": "grammar", "lemma": "Genitiv", "definition": "Besitz und Zugehörigkeit.",
        "examples": ["Das Auto meines Bruders."]}).json()["card"]
    assert [c["lemma"] for c in client.get("/api/cards").json()["cards"]] == ["Genitiv"]
    assert client.get("/api/cards?q=Genitiv").json()["cards"]
    patched = client.patch(f"/api/cards/{created['id']}",
                           json={"definition": "Neu erklärt."}).json()["card"]
    assert patched["definition"] == "Neu erklärt."


def test_answering_writes_a_review_log(client, tmp_path):
    card = client.post("/api/command", json={"text": "add katze"}).json()["card"]
    client.post("/api/answer", json={"id": card["id"], "grade": 2})
    log = tmp_path / "reviews.csv"
    assert log.exists()
    lines = log.read_text(encoding="utf-8").strip().splitlines()
    assert lines[0].startswith("ts,card,direction,grade")
    assert lines[1].split(",")[1:5] == ["katze", "forward", "2", "new"]


def test_history_endpoint_shape(client):
    card = client.post("/api/command", json={"text": "add katze"}).json()["card"]
    for _ in range(3):
        client.post("/api/answer", json={"id": card["id"], "grade": 3})
    data = client.get("/api/history?days=14&forecast_days=7").json()
    assert len(data["activity"]) == 14
    assert len(data["retention"]) == 14
    assert len(data["learned"]) == 14
    assert len(data["forecast"]) == 7
    assert len(data["intervals"]) == 6
    assert data["activity"][-1]["reviews"] == 3
    assert data["summary"]["reviews_today"] == 3
    assert data["summary"]["logged_total"] == 3
    assert data["learned"][-1]["forward"] == 1        # easy x3 puts it past 3 days


def test_history_works_on_an_untouched_deck(client):
    client.post("/api/command", json={"text": "add katze"})
    data = client.get("/api/history").json()
    assert data["summary"]["logged_total"] == 0
    assert all(point["value"] is None for point in data["retention"])
    assert data["intervals"][0]["count"] == 1          # one new card
    assert data["summary"]["logging_since"] is None


def test_history_day_window_is_clamped(client):
    assert len(client.get("/api/history?days=1").json()["activity"]) == 7
    assert len(client.get("/api/history?days=9000").json()["activity"]) == 120
