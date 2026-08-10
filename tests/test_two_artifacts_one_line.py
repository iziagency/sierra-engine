"""One line can ask for two documents, and each gets its own answer.

JC, live in the channel on 8.3:

    prep ONSIG2 CAP SP app and RTS Prog excel app

Two artifacts, one client, one sentence — and the engine saw a single job. He
asked for the Sierra application AND the Progressive workbook; each is a
separate file that has to come back with its own link, or the broker cannot
tell which one landed.

The client is named once and carries across the "and": the second half says
what to build, not who for.
"""
from __future__ import annotations

import slack_engine as se


def pairs(text):
    return [(j["doc"], j["sp"]) for j in se.parse_batch(text)]


# --------------------------------------------------------- his actual line

def test_his_line_yields_two_jobs_for_one_client():
    assert pairs("prep ONSIG2 CAP SP app and RTS Prog excel app") == [
        ("app", "ONSIG2"), ("rts", "ONSIG2")]


def test_the_order_he_wrote_them_in_is_kept():
    assert pairs("prep ONSIG2 CAP RTS Prog excel app and SP app") == [
        ("rts", "ONSIG2"), ("app", "ONSIG2")]


def test_an_ampersand_reads_the_same_as_and():
    assert pairs("prep NORAS CAP app & QP") == [("app", "NORAS"), ("qp", "NORAS")]


def test_three_artifacts_in_one_breath():
    got = pairs("prep FALCO1 CAP app and RTS Prog excel app and QP")
    assert got == [("app", "FALCO1"), ("rts", "FALCO1"), ("qp", "FALCO1")]


# ------------------------------------------------- SP names the Sierra version

def test_sp_before_app_is_the_sierra_application_not_a_reject():
    # The guide's reject rule is about labelling a QP; "SP app" is just how he
    # says "our own application".
    jobs = se.parse_batch("prep ONSIG2 CAP SP app")
    assert [(j["doc"], j["sp"]) for j in jobs] == [("app", "ONSIG2")]
    assert not jobs[0]["reject"]


# --------------------------------------------------- everything else survives

def test_one_artifact_is_still_one_job():
    assert pairs("Prep SUMMI1 CAP app") == [("app", "SUMMI1")]


def test_the_assignment_list_format_is_untouched():
    assert pairs("RTS Prog excel app> SOUTH5 CAP, HAMIL CAP") == [
        ("rts", "SOUTH5"), ("rts", "HAMIL")]


def test_a_sentence_with_and_but_one_artifact_stays_one_job():
    assert pairs("build the QP for FALCO1 and send it over") == [("qp", "FALCO1")]


def test_prose_is_still_not_a_command():
    assert se.parse_batch("the app and the QP are both missing loss runs") == []
