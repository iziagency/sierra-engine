"""The two things a broker types on Monday: a verb, and an SP code.

Both failed live on the 7.29 call. JC typed `create the QP for...` and
`prep FALCO1 cap Qp` — neither verb was in the list — and `build the QP for
FALCO1` found the command but not the client, because only names resolved a
dossier. He then told us plainly that his team speaks in codes, never names:
"we always want to use the SP name because it's our unique identifier".

He also dictated the format his team will actually use, and the verb sits at
the END of it:

    NORAS1 CAP RTS PROG EXCEL APP PREP
    FALCO1 CAP QP

So the grammar has to read a verb before the document, a verb after it, or a
bare task code with no verb at all.
"""
from __future__ import annotations

import json

import process_drop as pd
import slack_engine as se


def make_dossier(tmp_path, slug: str, sp_code: str, name: str, dba: str = ""):
    folder = tmp_path / slug
    folder.mkdir(parents=True)
    (folder / "state.json").write_text(
        json.dumps({"sp_code": sp_code,
                    "company": {"first_named_insured": name, "dba": dba}}),
        encoding="utf-8")
    return folder


# ------------------------------------------------------------ the verb list

def test_create_is_a_verb():
    # verbatim from the call: "create the QP for..."
    assert se.parse_assemble("create the QP for Falcon Ridge")["doc"] == "qp"


def test_prep_is_a_verb():
    # verbatim: "prep FALCO1 cap Qp" — note the mixed case he actually typed
    assert se.parse_assemble("prep FALCO1 cap Qp")["doc"] == "qp"


def test_put_together_is_a_verb():
    assert se.parse_assemble("put together the RTS for Lakeside")["doc"] == "rts"


def test_the_verbs_that_already_worked_still_work():
    for text in ("build the QP for FALCO1", "arma el QP de lakeside",
                 "llena el RTS de Amy", "assemble the QP"):
        assert se.parse_assemble(text) is not None, text


# --------------------------------------------- the task-code format he dictated

def test_his_dictated_rts_command():
    got = se.parse_assemble("NORAS1 CAP RTS PROG EXCEL APP PREP")
    assert got["doc"] == "rts"
    assert got["sp"] == "NORAS1"


def test_his_dictated_qp_command_has_no_verb_at_all():
    got = se.parse_assemble("FALCO1 CAP QP")
    assert got["doc"] == "qp"
    assert got["sp"] == "FALCO1"


def test_the_sp_code_is_read_out_of_a_prose_command_too():
    assert se.parse_assemble("build the QP for FALCO1")["sp"] == "FALCO1"


def test_a_code_without_a_digit_is_still_a_code():
    # Lakeside's real folder is LAKES — verified in JC's own Drive, no digit.
    assert se.parse_assemble("LAKES CAP QP")["sp"] == "LAKES"


# ---------------------------------------------------- what must NOT be a command

def test_talking_about_a_qp_is_not_asking_for_one():
    # A broker reporting a problem must not silently trigger a rebuild.
    assert se.parse_assemble("the FALCO1 CAP QP is missing the loss runs") is None


def test_a_plain_drop_is_not_a_command():
    assert se.parse_assemble("here's the app for Nora's Towing") is None


def test_empty_text_is_not_a_command():
    assert se.parse_assemble("") is None
    assert se.parse_assemble(None) is None


# ------------------------------------------------- the SP code resolves a client

def test_an_sp_code_finds_the_client(tmp_path, monkeypatch):
    monkeypatch.setattr(pd, "CLIENTS", tmp_path)
    make_dossier(tmp_path, "falcon-ridge-towing-llc", "FALCO1", "Falcon Ridge Towing LLC")
    assert pd.find_client_in_text("build the QP for FALCO1") == "falcon-ridge-towing-llc"


def test_a_lowercase_code_finds_the_client_too(tmp_path, monkeypatch):
    # He typed "prep FALCO1 cap Qp" with mixed case; brokers will type worse.
    monkeypatch.setattr(pd, "CLIENTS", tmp_path)
    make_dossier(tmp_path, "falcon-ridge-towing-llc", "FALCO1", "Falcon Ridge Towing LLC")
    assert pd.find_client_in_text("prep falco1 cap qp") == "falcon-ridge-towing-llc"


def test_the_code_wins_over_a_name_that_points_elsewhere(tmp_path, monkeypatch):
    # The code is the unique identifier; the name is the thing that arrives
    # misspelled. When both appear, the code decides.
    monkeypatch.setattr(pd, "CLIENTS", tmp_path)
    make_dossier(tmp_path, "falcon-ridge-towing-llc", "FALCO1", "Falcon Ridge Towing LLC")
    make_dossier(tmp_path, "lakeside-towing-llc", "LAKES", "Lakeside Towing LLC")
    assert pd.find_client_in_text("FALCO1 — the one lakeside sent") == "falcon-ridge-towing-llc"


def test_a_name_still_resolves_when_no_code_is_given(tmp_path, monkeypatch):
    monkeypatch.setattr(pd, "CLIENTS", tmp_path)
    make_dossier(tmp_path, "lakeside-towing-llc", "LAKES", "Lakeside Towing LLC")
    assert pd.find_client_in_text("el valor del camion de lakeside esta mal") \
        == "lakeside-towing-llc"


def test_a_longer_word_starting_with_the_code_is_not_the_code(tmp_path, monkeypatch):
    # "lakeside" starts with "LAKES" — matching on prefix would make every
    # mention of the company name look like a task code.
    monkeypatch.setattr(pd, "CLIENTS", tmp_path)
    make_dossier(tmp_path, "lakeside-towing-llc", "LAKES", "Lakeside Towing LLC")
    make_dossier(tmp_path, "santo-domingo-recovery", "LAKES2", "Santo Domingo Recovery")
    # resolves by name to Lakeside, not by a phantom LAKES2 code hit
    assert pd.find_client_in_text("lakeside") == "lakeside-towing-llc"


def test_two_different_codes_in_one_message_is_ambiguous(tmp_path, monkeypatch):
    monkeypatch.setattr(pd, "CLIENTS", tmp_path)
    make_dossier(tmp_path, "falcon-ridge-towing-llc", "FALCO1", "Falcon Ridge Towing LLC")
    make_dossier(tmp_path, "nora-s-towing-inc", "NORAS1", "Nora's Towing Inc")
    assert pd.find_client_in_text("FALCO1 and NORAS1") == ""


def test_an_unknown_code_resolves_to_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(pd, "CLIENTS", tmp_path)
    make_dossier(tmp_path, "falcon-ridge-towing-llc", "FALCO1", "Falcon Ridge Towing LLC")
    assert pd.find_client_in_text("build the QP for NEWCO1") == ""
