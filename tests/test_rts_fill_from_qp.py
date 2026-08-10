"""Tests for reports/rts_fill.py's --from-qp wiring (the apply_qp helper).

Kept in its own file, separate from test_rts_fill_wiring.py, since a parallel
change is touching rts_fill.py for value formatting - this file only
exercises the new QP-merge entry point (a new function + a new argparse
flag), not formatting behavior.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import qp_read
import rts_fill

ROOT = Path(__file__).resolve().parent.parent
REAL_QP = ROOT / "reports" / "out" / "lakeside-towing-llc" / "LAKES CAP tow QP 7.27.26.pdf"

pytestmark = pytest.mark.skipif(
    not REAL_QP.exists(), reason="real Lakeside QP fixture not present in this checkout")


def test_apply_qp_writes_a_dossier_shaped_state_file(tmp_path, monkeypatch):
    monkeypatch.setattr(rts_fill, "CLIENTS", tmp_path)
    warnings = rts_fill.apply_qp("lakeside-towing-llc", str(REAL_QP))

    state_path = tmp_path / "lakeside-towing-llc" / "state.json"
    assert state_path.exists()
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["company"]["dba"] == "Lakeside Towing LLC"
    assert state["company"]["fein"] == "85.0604178"
    assert len(state["vehicles"]) == 1
    assert len(state["drivers"]) == 1
    assert isinstance(warnings, list)
    assert any("85.0604178" in w for w in warnings)


def test_apply_qp_merges_onto_existing_state_without_wiping_it(tmp_path, monkeypatch):
    monkeypatch.setattr(rts_fill, "CLIENTS", tmp_path)
    folder = tmp_path / "lakeside-towing-llc"
    folder.mkdir(parents=True)
    (folder / "state.json").write_text(
        json.dumps({"_identifier_notes": ["pre-existing human note, not in any QP"]}),
        encoding="utf-8")

    rts_fill.apply_qp("lakeside-towing-llc", str(REAL_QP))

    state = json.loads((folder / "state.json").read_text(encoding="utf-8"))
    assert state["_identifier_notes"] == ["pre-existing human note, not in any QP"]
    assert state["company"]["dba"] == "Lakeside Towing LLC"


def test_apply_qp_on_a_malformed_pdf_raises_qp_read_error_not_a_generic_crash(tmp_path, monkeypatch):
    monkeypatch.setattr(rts_fill, "CLIENTS", tmp_path)
    fake = tmp_path / "fake.pdf"
    fake.write_text("not a pdf", encoding="utf-8")
    with pytest.raises(qp_read.QPReadError):
        rts_fill.apply_qp("some-client", str(fake))


def test_apply_qp_never_touches_the_real_client_folder(tmp_path, monkeypatch):
    monkeypatch.setattr(rts_fill, "CLIENTS", tmp_path)
    real_state = ROOT / "app-form" / "clients" / "lakeside-towing-llc" / "state.json"
    before = real_state.read_text(encoding="utf-8")

    rts_fill.apply_qp("lakeside-towing-llc", str(REAL_QP))

    after = real_state.read_text(encoding="utf-8")
    assert before == after
