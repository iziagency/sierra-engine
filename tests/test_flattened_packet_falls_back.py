"""A packet that cannot be read as a form is still the client's paperwork.

Live on 8.3, with JC watching the channel: `Prep ONSIG2 CAP app` found the
folder, downloaded `ONSIG2 CAP auh QP 8.3.26.pdf`, and stopped —

    QPReadError: does not look like a Sierra Pacific QP - only 0 page(s)
    carry AcroForm form
    Happy to build it — but I don't know which client.

The file was a perfectly good Sierra Pacific application; it had simply been
flattened, so its answers are ink rather than form fields. The fast path was
right to decline. Abandoning the client was not: the same PDF read as material
gives the same facts, just slower and at the cost of a model call.

Fast path first, always. Falling back is a downgrade, so it is announced.
"""
from __future__ import annotations

import slack_engine as se


def test_a_flattened_packet_is_read_as_material_instead():
    assert se.should_fall_back_to_reading(
        "does not look like a Sierra Pacific QP - only 0 page(s) carry "
        "AcroForm form") is True


def test_a_packet_with_some_fields_is_not_a_fallback_case():
    # Partial reads are the fast path's job to handle; re-reading with a model
    # would throw away the fields Sierra already typed.
    assert se.should_fall_back_to_reading(
        "page 9 (loss runs) has 26 widgets, expected 35") is False


def test_a_missing_file_is_not_a_fallback_case():
    assert se.should_fall_back_to_reading(
        "[Errno 2] No such file or directory") is False


def test_an_empty_reason_never_triggers_a_model_call():
    assert se.should_fall_back_to_reading("") is False
    assert se.should_fall_back_to_reading(None) is False
