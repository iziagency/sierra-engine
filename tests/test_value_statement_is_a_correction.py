"""«the FEIN is 12-3456789» is a correction, and the bot's own words say so.

When a broker rejects a change, the engine replies: "Tell me the right value
(e.g. 'the FEIN is 12-3456789')". That exact phrasing then failed
carries_instruction() and took the update path instead of the surgical one —
the engine recommending a sentence it does not understand.

Widening the detector has to stay narrow, because the cost of a false positive
is real: material read as a correction loses everything the message carried
beyond the named field. So the shape is deliberately tight — a SHORT message
naming a field and a value that contains a digit. Anything long, or any value
without a number in it, stays on the material path where it belongs.
"""
from __future__ import annotations

from process_drop import carries_instruction


def matches(text: str) -> bool:
    return bool(carries_instruction(text))


# ----------------------------------------------------- these are corrections

def test_the_phrasing_the_bot_itself_suggests():
    assert matches("the FEIN is 12-3456789")


def test_the_spanish_form():
    assert matches("el FEIN es 88-3410999")


def test_a_phone_correction():
    assert matches("el telefono es 805-555-0147")


def test_a_value_with_units():
    assert matches("el radio es 100 millas")


def test_still_matches_the_keywords_that_already_worked():
    for t in ("cambia el FEIN a 88-3410999", "el FEIN esta mal, es 88-3410999",
              "replace the 2012 truck", "remove driver Luis Ferrer"):
        assert matches(t), t


# ------------------------------------------------- these are NOT corrections

def test_a_full_new_client_intake_is_material_not_a_correction():
    # The Shoreline intake: long, full of values, and every word of it matters.
    # Reading it as "correct one field" would throw the rest away.
    assert not matches(
        "New tow client, called in this morning.\n"
        "Shoreline Towing & Recovery LLC, dba Shoreline Towing\n"
        "Owner Marcos Delgado, born 4/12/1979\n"
        "1420 Harbor Blvd, Oxnard, CA 93035\n"
        "FEIN 88-3410772, USDOT 3902114")


def test_a_statement_with_no_number_is_not_a_value_correction():
    assert not matches("the client is a tow operator")
    assert not matches("el cliente es de California")


def test_a_plain_question_is_not_a_correction():
    assert not matches("what is the FEIN on this one?")


def test_a_sentence_that_merely_contains_a_number_is_not_a_correction():
    # Caught by probing: short enough, one line, and a digit somewhere in a
    # long trailing clause. A correction names ONE value, briefly.
    assert not matches(
        "this is client number 3 of the day and the rest is attached")
    assert not matches("esto es lo que me mando el cliente numero 2")


def test_a_drop_note_is_not_a_correction():
    assert not matches("adjunto el email que me llego de este cliente")


def test_empty_text_is_not_a_correction():
    assert not matches("")
    assert not matches(None)
