"""The generator talks to the API; here we check the parsing around it."""
import json

import pytest

from flashcard.generator import (ClaudeGenerator, GeneratorError, _extract_json,
                                 _normalise, guess_type)


def test_extract_json_from_a_fenced_block():
    text = """Hier ist die Karte:\n```json\n{"lemma": "Katze", "type": "noun"}\n```"""
    assert _extract_json(text)["lemma"] == "Katze"


def test_extract_json_with_surrounding_prose():
    assert _extract_json('Klar! {"lemma": "Hund"} Viel Erfolg!')["lemma"] == "Hund"


def test_extract_json_raises_on_garbage():
    with pytest.raises(GeneratorError):
        _extract_json("Tut mir leid, das kann ich nicht.")


def test_normalise_german_type_names_and_stray_article():
    got = _normalise({"type": "Nomen", "lemma": "die katze", "article": "",
                      "tags": "tier, haustier"})
    assert got["type"] == "noun"
    assert got["article"] == "die"
    assert got["lemma"] == "Katze"
    assert got["tags"] == ["tier", "haustier"]


def test_normalise_rejects_nonsense_values():
    got = _normalise({"type": "banana", "article": "dei", "aux": "werden", "level": "Z9"})
    assert got["type"] == "banana" or got["type"]  # unknown types survive for the editor
    assert got["article"] == "" and got["aux"] == "" and got["level"] == "B1"


def test_generate_card_uses_known_vocabulary_in_the_prompt(monkeypatch):
    seen = {}

    def fake_call(self, prompt, max_tokens=900):
        seen["prompt"] = prompt
        return json.dumps({"type": "verb", "lemma": "laufen", "praeteritum": "lief",
                           "perfekt": "ist gelaufen", "aux": "sein",
                           "definition": "sich schnell bewegen", "example": "Er lief zur Behörde."})

    monkeypatch.setattr(ClaudeGenerator, "_call", fake_call)
    card = ClaudeGenerator(api_key="test").generate_card(
        "laufen", known=["die Behörde", "der Termin"], topics=["Passiv Präsens"])
    assert card["perfekt"] == "ist gelaufen"
    assert "die Behörde" in seen["prompt"]
    assert "Passiv Präsens" in seen["prompt"]


def test_generate_sentence_asks_for_something_different(monkeypatch):
    seen = {}

    def fake_call(self, prompt, max_tokens=400):
        seen["prompt"] = prompt
        return '{"example": "Die Katze sitzt auf dem Vertrag."}'

    monkeypatch.setattr(ClaudeGenerator, "_call", fake_call)
    out = ClaudeGenerator(api_key="test").generate_sentence(
        {"lemma": "Katze"}, avoid="Die Katze schläft.")
    assert out == "Die Katze sitzt auf dem Vertrag."
    assert "Die Katze schläft." in seen["prompt"]


def test_no_api_key_gives_a_helpful_error():
    with pytest.raises(GeneratorError, match="ANTHROPIC_API_KEY"):
        ClaudeGenerator(api_key="").generate_card("Katze")


@pytest.mark.parametrize("word,expected", [
    ("Katze", "noun"), ("laufen", "verb"), ("Bescheid sagen", "phrase"), ("schnell", "other"),
])
def test_offline_type_guess(word, expected):
    assert guess_type(word) == expected


def test_workspace_id_is_sent_when_configured(monkeypatch):
    """Identity-linked keys are rejected without the workspace header."""
    import json as _json
    import urllib.request

    captured = {}

    class FakeResponse:
        def read(self):
            return _json.dumps({"content": [{"type": "text", "text": '{"example": "Hallo."}'}]}).encode()

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(request, timeout=None):
        captured["headers"] = dict(request.headers)
        return FakeResponse()

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    gen = ClaudeGenerator(api_key="test", workspace_id="wrkspc_01ABC")
    gen.generate_sentence({"lemma": "Katze"})
    # urllib title-cases header names
    assert captured["headers"]["Anthropic-workspace-id"] == "wrkspc_01ABC"

    captured.clear()
    ClaudeGenerator(api_key="test", workspace_id="").generate_sentence({"lemma": "Katze"})
    assert "Anthropic-workspace-id" not in captured["headers"]


def test_workspace_error_is_explained(monkeypatch):
    import urllib.error
    import urllib.request

    body = ('{"type":"error","error":{"type":"invalid_request_error","message":'
            '"anthropic-workspace-id is required when authenticating with an identity-linked API key"}}')

    def boom(request, timeout=None):
        raise urllib.error.HTTPError("u", 400, "Bad Request", {}, __import__("io").BytesIO(body.encode()))

    monkeypatch.setattr(urllib.request, "urlopen", boom)
    with pytest.raises(GeneratorError, match="ANTHROPIC_WORKSPACE_ID"):
        ClaudeGenerator(api_key="test").generate_card("Katze")


def test_other_http_errors_stay_readable(monkeypatch):
    import io
    import urllib.error
    import urllib.request

    def boom(request, timeout=None):
        raise urllib.error.HTTPError("u", 401, "Unauthorized", {},
                                     io.BytesIO(b'{"error":{"message":"invalid x-api-key"}}'))

    monkeypatch.setattr(urllib.request, "urlopen", boom)
    with pytest.raises(GeneratorError, match="401"):
        ClaudeGenerator(api_key="bad").generate_card("Katze")


def test_generate_examples_returns_three_sentences(monkeypatch):
    def fake_call(self, prompt, max_tokens=700):
        assert "drei" in prompt or "3" in prompt
        return '{"examples": ["Satz eins.", "Satz zwei.", "Satz drei."]}'

    monkeypatch.setattr(ClaudeGenerator, "_call", fake_call)
    out = ClaudeGenerator(api_key="test").generate_examples({"lemma": "Passiv"})
    assert out == ["Satz eins.", "Satz zwei.", "Satz drei."]


def test_normalise_keeps_grammar_examples():
    got = _normalise({"type": "grammatik", "lemma": "Passiv",
                      "examples": ["Eins.", "  ", "Zwei."]})
    assert got["type"] == "grammar"
    assert got["examples"] == ["Eins.", "Zwei."]
