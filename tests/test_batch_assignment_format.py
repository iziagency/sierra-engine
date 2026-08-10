"""Allyssa's format: the one the person who actually assigns work uses.

Verbatim from Slack, 7.31.26:

    QP build> NORAS CAP, CARRS CAP, ONSIG2 CAP, BROOK2 CAP
    RTS Prog excel app> SOUTH5 CAP, HAMIL CAP, NORAS CAP, ONSIG2 CAP
    Please do the Prog excel apps in that order first please.

Three things neither of the formats we already supported allows: the artifact
comes BEFORE the verb ("QP build"), `>` introduces the list, and one line names
FOUR clients. The engine understood none of it.

Order is part of the instruction — she said so explicitly — so a batch comes
back in the sequence she wrote it, never sorted or deduped into something
tidier.
"""
from __future__ import annotations

import slack_engine as se


def codes(text):
    return [j["sp"] for j in se.parse_batch(text)]


def docs(text):
    return [j["doc"] for j in se.parse_batch(text)]


# --------------------------------------------------------- her actual lines

def test_the_rts_line_yields_four_jobs_in_her_order():
    line = "RTS Prog excel app> SOUTH5 CAP, HAMIL CAP, NORAS CAP, ONSIG2 CAP"
    assert codes(line) == ["SOUTH5", "HAMIL", "NORAS", "ONSIG2"]
    assert set(docs(line)) == {"rts"}


def test_the_qp_line_yields_four_jobs_in_her_order():
    line = "QP build> NORAS CAP, CARRS CAP, ONSIG2 CAP, BROOK2 CAP"
    assert codes(line) == ["NORAS", "CARRS", "ONSIG2", "BROOK2"]
    assert set(docs(line)) == {"qp"}


def test_both_lines_in_one_message_keep_their_own_artifact():
    msg = ("Some files to get working on:\n\n"
           "QP build> NORAS CAP, CARRS CAP\n\n"
           "RTS Prog excel app> SOUTH5 CAP, HAMIL CAP\n\n"
           "Please do the Prog excel apps in that order first please.")
    jobs = se.parse_batch(msg)
    assert [(j["doc"], j["sp"]) for j in jobs] == [
        ("qp", "NORAS"), ("qp", "CARRS"), ("rts", "SOUTH5"), ("rts", "HAMIL")]


def test_a_single_client_after_the_arrow_still_works():
    assert codes("QP build> NORAS CAP") == ["NORAS"]


# ------------------------------------------------- codes exactly as she wrote

def test_a_code_with_no_digit_is_not_helpfully_corrected():
    # NORAS is the real code in JC's book; appending a 1 invents a new client.
    assert codes("RTS Prog excel app> NORAS CAP") == ["NORAS"]


def test_a_code_with_a_digit_keeps_it():
    assert codes("QP build> BROOK2 CAP") == ["BROOK2"]


# ---------------------------------------- the formats we already had survive

def test_the_written_guide_form_still_parses_as_one_job():
    jobs = se.parse_batch("Prep SUMMI1 CAP app")
    assert [(j["doc"], j["sp"]) for j in jobs] == [("app", "SUMMI1")]


def test_the_dictated_verb_last_form_still_parses():
    jobs = se.parse_batch("NORAS1 CAP RTS PROG EXCEL APP PREP")
    assert [(j["doc"], j["sp"]) for j in jobs] == [("rts", "NORAS1")]


def test_prose_still_parses_as_one_job():
    jobs = se.parse_batch("build the QP for FALCO1")
    assert [(j["doc"], j["sp"]) for j in jobs] == [("qp", "FALCO1")]


# ------------------------------------------------ what must still be nothing

def test_her_covering_sentence_is_not_a_job():
    assert se.parse_batch("Please do the Prog excel apps in that order first "
                          "please.") == []


def test_a_greeting_is_not_a_batch():
    assert se.parse_batch("Hello! Thank you for all your help!") == []


def test_prose_about_a_qp_is_still_not_a_command():
    assert se.parse_batch("the FALCO1 CAP QP is missing the loss runs") == []


def test_empty_text_yields_no_jobs():
    assert se.parse_batch("") == []
    assert se.parse_batch(None) == []
