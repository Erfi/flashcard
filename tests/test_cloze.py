"""Blanking the target word out of the example sentence."""
import pytest

from flashcard.cloze import mask
from flashcard.models import Card


def card(**kw) -> Card:
    return Card.from_dict(kw)


def test_noun_takes_its_article_with_it():
    c = card(type="noun", article="die", lemma="Katze", plural="die Katzen",
             example="Die Katze schläft auf dem Sofa.")
    assert mask(c) == "____ schläft auf dem Sofa."


def test_plural_form_is_recognised():
    c = card(type="noun", article="der", lemma="Vertrag", plural="die Verträge",
             example="Beide Verträge liegen schon auf dem Tisch.")
    assert "Verträge" not in mask(c)


def test_inflected_noun_is_caught_by_the_stem():
    c = card(type="noun", article="die", lemma="Wohnung", plural="die Wohnungen",
             example="In der Wohnungen Nähe gibt es viele Geschäfte.")
    assert "Wohnung" not in mask(c)


def test_strong_verb_forms_are_masked():
    c = card(type="verb", lemma="laufen", praesens_3sg="läuft", praeteritum="lief",
             perfekt="ist gelaufen", aux="sein",
             example="Gestern lief sie zehn Kilometer durch den Park.")
    assert mask(c) == "Gestern ____ sie zehn Kilometer durch den Park."


def test_separable_verb_loses_its_prefix_too():
    c = card(type="verb", lemma="anrufen", praesens_3sg="ruft an", praeteritum="rief an",
             perfekt="hat angerufen", aux="haben", separable=True,
             example="Ruf mich bitte heute Abend nach acht an.")
    masked = mask(c)
    assert masked.startswith("____")
    assert not masked.rstrip(".").endswith("an")


def test_a_real_preposition_survives():
    c = card(type="verb", lemma="ankommen", praesens_3sg="kommt an", praeteritum="kam an",
             perfekt="ist angekommen", aux="sein", separable=True,
             example="Der Zug kommt an Gleis drei an.")
    masked = mask(c)
    assert "an Gleis drei" in masked


def test_reflexive_pronoun_stays_visible():
    c = card(type="verb", lemma="sich freuen", praesens_3sg="freut sich",
             praeteritum="freute sich", perfekt="hat sich gefreut", aux="haben",
             example="Meine Tochter freut sich sehr auf die Ferien.")
    masked = mask(c)
    assert "freut" not in masked
    assert "sich" in masked


def test_eln_verbs_lose_the_e_when_conjugated():
    c = card(type="verb", lemma="bezweifeln", praesens_3sg="bezweifelt",
             praeteritum="bezweifelte", perfekt="hat bezweifelt", aux="haben",
             example="Ich bezweifle, dass dieser Plan funktioniert.")
    assert "bezweifle" not in mask(c)


def test_adjective_is_masked_in_place():
    c = card(type="adjective", lemma="müde", example="Nach der Arbeit sind wir sehr müde.")
    assert mask(c) == "Nach der Arbeit sind wir sehr ____."


def test_sentence_without_the_word_is_left_alone():
    c = card(type="noun", article="die", lemma="Kaution", plural="die Kautionen",
             example="Nach dem Auszug kam das Geld vollständig zurück.")
    assert mask(c) == "Nach dem Auszug kam das Geld vollständig zurück."


def test_a_sentence_that_would_become_only_blanks_is_kept():
    c = card(type="phrase", lemma="Bescheid sagen", example="Bescheid sagen!")
    assert mask(c) == "Bescheid sagen!"


def test_empty_example_stays_empty():
    assert mask(card(type="noun", lemma="Katze", example="")) == ""


def test_masked_sentence_starts_with_a_capital():
    c = card(type="noun", article="der", lemma="Zug", plural="die Züge",
             example="Der Zug hatte zwanzig Minuten Verspätung.")
    assert mask(c).startswith("____ hatte")


@pytest.mark.parametrize("level", ["a2", "b1"])
def test_every_seed_example_can_be_blanked(level):
    import yaml
    from flashcard.paths import SEED_DECKS
    cards = [Card.from_dict(c) for c in
             yaml.safe_load(SEED_DECKS[level].read_text(encoding="utf-8"))["cards"]]
    vocab = [c for c in cards if c.type != "grammar"]
    untouched = [c.lemma for c in vocab if mask(c) == c.example]
    assert untouched == [], f"kein Lückentext möglich für: {untouched}"
