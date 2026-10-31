import pytest

from facebook import adapters, ai

CFG = {"gate": {"require_any": ["need", "looking for", "automate"],
                "skip_if_contains": ["hire me", "dm me"]}}


def test_text_hash_ignores_case_spacing_and_punctuation():
    assert adapters.text_hash("Need a Dev!!") == adapters.text_hash("need   a dev")
    assert adapters.text_hash("need a dev") != adapters.text_hash("need a designer")


def test_gate_keeps_asks_and_drops_pitches():
    assert adapters.passes_gate("Looking for someone to build a Shopify app", CFG)
    assert adapters.passes_gate("How can I automate copying orders into Xero?", CFG)
    assert not adapters.passes_gate("Hire me, I build Shopify apps", CFG)
    assert not adapters.passes_gate("Nice weather in Goa today", CFG)
    assert not adapters.passes_gate("Need clients? My students made $10k in 30 days", CFG)
    assert not adapters.passes_gate("I need help and I made $3,200 so far this month", CFG)


def test_gate_without_required_words_lets_everything_through():
    assert adapters.passes_gate("anything at all", {"gate": {}})


def test_parse_json_tolerates_thinking_and_fences():
    raw = '<think>let me see</think>```json\n{"score": "72", "intent": "problem"}\n```\nthanks!'
    assert ai.parse_json(raw) == {"score": "72", "intent": "problem"}


def test_parse_json_rejects_prose():
    with pytest.raises(ValueError):
        ai.parse_json("I think this is a good lead.")


def test_clamp():
    assert ai._clamp("85") == 85
    assert ai._clamp(140) == 100
    assert ai._clamp(None) == 0
