import pytest

from flashcard.commands import parse


@pytest.mark.parametrize("text,expected", [
    ("add katze", {"action": "add", "query": "katze", "hint_type": ""}),
    ("add verb: laufen", {"action": "add", "query": "laufen", "hint_type": "verb"}),
    ("add grammar: Konjunktiv II", {"action": "add", "query": "Konjunktiv II", "hint_type": "grammar"}),
    ("add nomen Katze", {"action": "add", "query": "Katze", "hint_type": "noun"}),
    ("katze", {"action": "add", "query": "katze", "hint_type": ""}),
])
def test_add_forms(text, expected):
    assert parse(text) == expected


def test_settings_commands():
    assert parse("set target 2026-09-18")["values"] == {"target_date": "2026-09-18"}
    assert parse("set new 40")["values"] == {"daily_new_limit": 40}
    assert parse("set reviews 4")["values"] == {"reviews_before_target": 4.0}
    assert parse("set target off")["values"] == {"target_date": None}


def test_bad_date_is_rejected():
    assert parse("set target 18.09.2026")["action"] == "error"


@pytest.mark.parametrize("text,action", [
    ("lernen", "review"), ("stats", "stats"), ("help", "help"),
    ("find woh", "find"), ("del katze", "delete"), ("satz katze", "sentence"),
    ("export", "export"), ("", "noop"),
])
def test_simple_commands(text, action):
    assert parse(text)["action"] == action


def test_unknown_command_is_reported():
    assert parse("!!! ??? %%%")["action"] == "error"
