"""The written grammar: Task Assignment Coding Guide v1.0 (arrived 8.1.26).

Word order is mandatory and the verb now leads:

    TASK · SP NAME · POLICY · VENDOR (optional) · ARTIFACT

    Prep SUMMI1 CAP app                  -> Sierra Pacific CAP application
    Prep FALCO1 CAP QP                   -> Sierra Pacific quoting packet
    Prep FALCO1 CAP AUW QP               -> AUW's version of the packet
    Prep NORAS1 CAP RTS PROG Excel app   -> Progressive workbook, RTS spec

Standing rules from the same page: the vendor slot alone changes the version
("FALCO1 CAP SP QP is a reject" — never label the Sierra version); the SP name
is the only search key, never the DBA; blank beats wrong — if you cannot fill
a slot, stop and ask.

The verb-LAST form he dictated on the 7.29 call ("NORAS1 CAP RTS PROG EXCEL
APP PREP") stays valid — his team heard it on that call and will type it.
"""
from __future__ import annotations

import slack_engine as se


# ------------------------------------------------------- the worked examples

def test_prep_summi1_cap_app_builds_the_sierra_app():
    got = se.parse_assemble("Prep SUMMI1 CAP app")
    assert got is not None
    assert got["doc"] == "app"
    assert got["sp"] == "SUMMI1"


def test_prep_falco1_cap_qp_builds_the_sierra_packet():
    got = se.parse_assemble("Prep FALCO1 CAP QP")
    assert got["doc"] == "qp"
    assert got["sp"] == "FALCO1"
    assert not got.get("vendor")


def test_the_vendor_slot_changes_the_version():
    got = se.parse_assemble("Prep FALCO1 CAP AUW QP")
    assert got["doc"] == "qp"
    assert got["sp"] == "FALCO1"
    assert got["vendor"] == "AUW"


def test_the_progressive_workbook_example():
    got = se.parse_assemble("Prep NORAS1 CAP RTS PROG Excel app")
    assert got["doc"] == "rts"
    assert got["sp"] == "NORAS1"


def test_update_is_a_task_verb_too():
    got = se.parse_assemble("Update SUMMI1 CAP app")
    assert got is not None
    assert got["doc"] == "app"
    assert got["task"] == "update"


# ------------------------------------------------------------ standing rules

def test_sp_qp_is_a_reject_and_says_why():
    # "Never label the Sierra version — FALCO1 CAP SP QP is a reject."
    got = se.parse_assemble("Prep FALCO1 CAP SP QP")
    assert got is not None
    assert got.get("reject"), "the reject must be caught, not built"
    assert "SP" in got["reject"]


def test_an_unknown_vendor_is_reported_not_guessed():
    # Blank beats wrong: an unwired vendor stops and asks instead of quietly
    # producing the Sierra version under a vendor's name.
    got = se.parse_assemble("Prep FALCO1 CAP TUMI QP")
    assert got is not None
    assert got.get("vendor") == "TUMI"
    assert got.get("unsupported_vendor")


# ------------------------------------- the dictated verb-last form still works

def test_the_call_dictated_form_survives_the_guide():
    got = se.parse_assemble("NORAS1 CAP RTS PROG EXCEL APP PREP")
    assert got["doc"] == "rts"
    assert got["sp"] == "NORAS1"


def test_bare_falco1_cap_qp_still_works():
    got = se.parse_assemble("FALCO1 CAP QP")
    assert got["doc"] == "qp"
    assert got["sp"] == "FALCO1"


# ----------------------------------------------------- what is still not a command

def test_prose_about_an_app_is_still_not_a_command():
    assert se.parse_assemble("the SUMMI1 CAP app is missing the loss runs") is None


def test_a_drop_with_the_word_app_is_not_a_command():
    assert se.parse_assemble("here's the app for Nora's Towing") is None


def test_a_correction_mentioning_both_verb_and_qp_apart_is_not_a_command():
    # Verb and artifact in different clauses: this is a correction, and
    # rebuilding the packet instead of applying it would look like obedience
    # while ignoring the instruction.
    assert se.parse_assemble(
        "update the carrier to MSIG for lakeside — the numbers in the QP "
        "from last month show Progressive and that's stale") is None
