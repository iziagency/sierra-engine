"""The command executor honours the guide before it builds anything.

parse_assemble is tested word by word elsewhere; this covers what
try_assemble_command DOES with the parse: rejects answer without building,
unwired vendors stop with an explanation, an unknown SP code asks instead of
guessing, and the SP-code resolver picks the client before any name does.
"""
from __future__ import annotations

import json

import process_drop as pd
import slack_engine as se


class Say:
    def __init__(self):
        self.messages = []

    def __call__(self, text="", thread_ts=None, **kw):
        self.messages.append(text)

    @property
    def all_text(self):
        return "\n".join(self.messages)


def make_dossier(tmp_path, slug, sp_code, name):
    d = tmp_path / slug
    d.mkdir(parents=True, exist_ok=True)
    (d / "state.json").write_text(json.dumps(
        {"sp_code": sp_code, "company": {"first_named_insured": name}}),
        encoding="utf-8")


def test_the_sp_reject_answers_without_building(tmp_path, monkeypatch):
    say = Say()
    handled = se.try_assemble_command("Prep FALCO1 CAP SP QP", "", say, "t1")
    assert handled is True
    assert "never labels the Sierra version" in say.all_text
    assert ":package:" not in say.all_text


def test_an_unwired_vendor_stops_and_explains(tmp_path, monkeypatch):
    say = Say()
    handled = se.try_assemble_command("Prep FALCO1 CAP TUMI QP", "", say, "t1")
    assert handled is True
    assert "TUMI" in say.all_text
    assert "isn't wired" in say.all_text
    assert ":package:" not in say.all_text      # no build was started


def test_an_unknown_sp_code_asks_naming_the_code(tmp_path, monkeypatch):
    monkeypatch.setattr(pd, "CLIENTS", tmp_path)
    say = Say()
    handled = se.try_assemble_command("Prep NEWCO1 CAP QP", "", say, "t1")
    assert handled is True
    assert "NEWCO1" in say.all_text
    assert ":package:" not in say.all_text


def test_the_code_resolves_the_client_before_any_name(tmp_path, monkeypatch):
    monkeypatch.setattr(pd, "CLIENTS", tmp_path)
    make_dossier(tmp_path, "falcon-ridge-towing-llc", "FALCO1",
                 "Falcon Ridge Towing LLC")
    say = Say()
    calls = []

    import qp_build
    monkeypatch.setattr(qp_build, "build",
                        lambda slug, risk, to_drive=True:
                        calls.append(slug) or
                        {"ok": True, "name": "FALCO1 CAP tow QP 8.1.26.pdf",
                         "pages": 16, "complete": False, "gate": [], "drive": ""})

    handled = se.try_assemble_command("Prep FALCO1 CAP QP", "", say, "t1")
    assert handled is True
    assert calls == ["falcon-ridge-towing-llc"]
    assert "FALCO1 CAP tow QP 8.1.26.pdf" in say.all_text


def test_a_finished_build_carries_the_notion_stamp(tmp_path, monkeypatch):
    monkeypatch.setattr(pd, "CLIENTS", tmp_path)
    make_dossier(tmp_path, "falcon-ridge-towing-llc", "FALCO1",
                 "Falcon Ridge Towing LLC")
    say = Say()

    import qp_build
    monkeypatch.setattr(qp_build, "build",
                        lambda slug, risk, to_drive=True:
                        {"ok": True, "name": "FALCO1 CAP tow QP 8.1.26.pdf",
                         "pages": 16, "complete": False, "gate": [], "drive": ""})
    # the stamp reads the dossier through se.HERE — point it at the tmp tree
    monkeypatch.setattr(se, "HERE", tmp_path.parent / "x", raising=False)

    se.try_assemble_command("Prep FALCO1 CAP QP", "", say, "t1")
    assert "Notion stamp" in say.all_text
    assert "@ Broker Qs ans in + LRs in" in say.all_text
