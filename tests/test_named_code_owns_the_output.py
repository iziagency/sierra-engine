"""A named SP code decides where the work goes. Nothing else gets a vote.

The resolution order read:

    slug_for_sp_code(code) or known_slug or find_client_in_text(text)

So a command naming a client we have no local file for — `Prep CARRS CAP app`,
typed in a thread that was about Lakeside — fell through to the THREAD, and
Lakeside's folder received Carrs Towing's application. The Drive lookup that
would have found CARRS never ran, because `known_slug` had already satisfied
the `or`.

Rule: if the broker named a code, that code is the client. Local file first,
then Sierra's Drive, and only a command with NO code at all may fall back to
the thread it was typed in.
"""
from __future__ import annotations

import json

import process_drop as pd
import slack_engine as se


class Say:
    def __init__(self):
        self.msgs = []

    def __call__(self, text="", thread_ts=None, **kw):
        self.msgs.append(text)

    @property
    def all(self):
        return "\n".join(self.msgs)


def dossier(tmp_path, slug, code):
    d = tmp_path / slug
    d.mkdir(parents=True, exist_ok=True)
    (d / "state.json").write_text(json.dumps(
        {"sp_code": code, "company": {"first_named_insured": slug}}),
        encoding="utf-8")


def test_a_named_code_never_loses_to_the_thread(tmp_path, monkeypatch):
    monkeypatch.setattr(pd, "CLIENTS", tmp_path)
    dossier(tmp_path, "lakeside-towing-llc", "LAKES")
    adopted = []
    monkeypatch.setattr(se, "_adopt_from_drive",
                        lambda code, say, ts: adopted.append(code) or "")

    say = Say()
    # CARRS has no local file; the thread is Lakeside's.
    se.try_assemble_command("Prep CARRS CAP QP", "lakeside-towing-llc", say, "t1")

    assert adopted == ["CARRS"], "Drive lookup must run for the named code"
    assert "lakeside" not in say.all.lower(), \
        "Lakeside's folder must never receive Carrs Towing's work"


def test_the_local_file_for_the_named_code_still_wins(tmp_path, monkeypatch):
    monkeypatch.setattr(pd, "CLIENTS", tmp_path)
    dossier(tmp_path, "carrs-towing", "CARRS")
    dossier(tmp_path, "lakeside-towing-llc", "LAKES")
    monkeypatch.setattr(se, "_adopt_from_drive",
                        lambda code, say, ts: pytest_fail())

    def pytest_fail():
        raise AssertionError("must not go to Drive when the file is local")

    calls = []
    import qp_build
    monkeypatch.setattr(qp_build, "build", lambda slug, risk, to_drive=True:
                        calls.append(slug) or {"ok": True, "name": "x.pdf",
                                               "pages": 1, "complete": False,
                                               "gate": [], "drive": ""})
    se.try_assemble_command("Prep CARRS CAP QP", "lakeside-towing-llc", Say(), "t1")
    assert calls == ["carrs-towing"]


def test_a_command_with_no_code_may_still_use_its_thread(tmp_path, monkeypatch):
    monkeypatch.setattr(pd, "CLIENTS", tmp_path)
    dossier(tmp_path, "lakeside-towing-llc", "LAKES")
    calls = []
    import qp_build
    monkeypatch.setattr(qp_build, "build", lambda slug, risk, to_drive=True:
                        calls.append(slug) or {"ok": True, "name": "x.pdf",
                                               "pages": 1, "complete": False,
                                               "gate": [], "drive": ""})
    se.try_assemble_command("build the QP", "lakeside-towing-llc", Say(), "t1")
    assert calls == ["lakeside-towing-llc"]


def test_a_batch_gives_each_job_its_own_client(tmp_path, monkeypatch):
    monkeypatch.setattr(pd, "CLIENTS", tmp_path)
    dossier(tmp_path, "noras-towing-inc", "NORAS")
    dossier(tmp_path, "carrs-towing", "CARRS")
    dossier(tmp_path, "lakeside-towing-llc", "LAKES")
    calls = []
    import qp_build
    monkeypatch.setattr(qp_build, "build", lambda slug, risk, to_drive=True:
                        calls.append(slug) or {"ok": True, "name": "x.pdf",
                                               "pages": 1, "complete": False,
                                               "gate": [], "drive": ""})
    se.try_assemble_command("QP build> NORAS CAP, CARRS CAP",
                            "lakeside-towing-llc", Say(), "t1")
    assert calls == ["noras-towing-inc", "carrs-towing"]
