"""Every finished build hands the broker its Notion close-out line.

Coding Guide v1.0: "When the build is done, stamp it in Notion in M6 form —
FALCO1 CAP tow QP prep plus your initials — and close with a pass code:
@ Broker Qs ans in + LRs in." The engine can't write into Sierra's Notion
without an integration token, so the reply carries the exact stamp instead —
zero reconstruction, zero drift from the M6 shape.
"""
from __future__ import annotations

import json

import slack_engine as se


def test_the_stamp_uses_the_sp_code_from_the_file(tmp_path, monkeypatch):
    slug = "falcon-ridge-towing-llc"
    root = tmp_path / "app-form" / "clients" / slug
    root.mkdir(parents=True)
    (root / "state.json").write_text(json.dumps({"sp_code": "FALCO1"}),
                                     encoding="utf-8")
    monkeypatch.setattr(se, "HERE", tmp_path / "watcher")

    line = se.notion_stamp_line(slug, "QP")
    assert "`FALCO1 CAP tow QP prep`" in line
    assert "@ Broker Qs ans in + LRs in" in line


def test_a_missing_dossier_falls_back_to_the_slug_not_a_crash(tmp_path, monkeypatch):
    monkeypatch.setattr(se, "HERE", tmp_path / "watcher")
    line = se.notion_stamp_line("ghost-client", "RTS Prog Excel app")
    assert "ghost-client CAP tow RTS Prog Excel app prep" in line
