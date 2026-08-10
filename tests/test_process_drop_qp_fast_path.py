"""Tests for the QP fast path in watcher/process_drop.py's claude_extract.

JC, on the recorded 7.22 call: "the first thing we want to do is we want to
be able to take that QP and populate the RTS... maybe you just upload the QP
directly into the Slack channel, use this as the basis for all the
information to fill out the RTS app." reports/qp_read.py already turns a QP's
AcroForm fields into a dossier dict for the CLI (rts_fill.py --from-qp), but
the Slack drop pipeline never used it: every QP dropped in Slack went through
claude_run — the whole PDF paid for at model-call time and cost, re-deriving
values a form field already states outright.

This fast path, added to claude_extract (the pipeline's single extraction
entry point), detects a QP by CONTENT — qp_read.QPReadError is the existing,
already-tested detector, reused here rather than inventing a second one, see
test_qp_read.py — and skips the model call entirely when nothing else in the
drop needs one.

Uses the real Lakeside QP fixture (verified in test_qp_read.py: 36 pages, 729
AcroForm widgets, 213 with values) for the happy-path tests, and tmp_path
synthetic PDFs for the non-QP / corrupt-file fallback tests, the same
convention test_qp_read.py's own error-handling tests already use.

`watcher` and `reports` are both already on sys.path via tests/conftest.py.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import process_drop

ROOT = Path(__file__).resolve().parent.parent
REAL_QP = ROOT / "reports" / "out" / "lakeside-towing-llc" / "LAKES CAP tow QP 7.27.26.pdf"

pytestmark = pytest.mark.skipif(
    not REAL_QP.exists(), reason="real Lakeside QP fixture not present in this checkout")


class FakeClaudeRun:
    """Stands in for process_drop.claude_run: records every prompt it was
    called with and always returns one fixed, valid extraction so whichever
    downstream step calls it (main extraction, vehicle sweep, identifier
    verification) gets something well-formed back, however many times it is
    called — none of that incidental call count is what this change is
    testing, only whether the model is reached AT ALL."""

    def __init__(self, payload: dict | None = None) -> None:
        self.prompts: list[str] = []
        self.payload = payload or {
            "company": {}, "vehicles": [], "_op": "add",
            "_summary": "fake model read", "red_flags": [],
        }

    def __call__(self, prompt: str, timeout: int | None = None) -> str:
        self.prompts.append(prompt)
        return json.dumps(self.payload)

    @property
    def call_count(self) -> int:
        return len(self.prompts)


def _forbidden_claude_run(prompt: str, timeout: int | None = None) -> str:
    raise AssertionError(
        "claude_run must not be called for a QP-only drop — skipping the "
        "model entirely is the whole point of this fast path")


# ---------------------------------------------------------------- QP-only: skip the model


def test_qp_only_drop_skips_the_model_entirely(monkeypatch):
    monkeypatch.setattr(process_drop, "claude_run", _forbidden_claude_run)

    data = process_drop.claude_extract("Here is Lakeside's QP.", [str(REAL_QP)], None)

    assert data["company"]["dba"] == "Lakeside Towing LLC"
    assert data["company"]["fein"] == "85.0604178"
    assert len(data["vehicles"]) == 1
    assert data["vehicles"][0]["vin"] == "5PVNJ8JN5G4S52079"
    assert len(data["drivers"]) == 1


def test_qp_only_drop_with_no_text_at_all_also_skips_the_model(monkeypatch):
    # The common real case: a bare attachment dropped with no message at all.
    monkeypatch.setattr(process_drop, "claude_run", _forbidden_claude_run)

    data = process_drop.claude_extract("", [str(REAL_QP)], None)

    assert data["company"]["dba"] == "Lakeside Towing LLC"


# ---------------------------------------------------------------- existing dossier: gap-fill, not overwrite


def test_qp_only_drop_with_existing_dossier_fills_gaps_and_keeps_existing_value(monkeypatch):
    monkeypatch.setattr(process_drop, "claude_run", _forbidden_claude_run)
    existing = {
        "coverages": {"total_stated_value": 55000},  # broker-corrected after the QP was made
        "company": {"how_found_sierra": "referral from Bob"},
        "_identifier_notes": ["a human note the QP has no idea about"],
    }

    data = process_drop.claude_extract("", [str(REAL_QP)], existing)

    # existing values survive...
    assert data["coverages"]["total_stated_value"] == 55000
    assert data["company"]["how_found_sierra"] == "referral from Bob"
    assert data["_identifier_notes"][0] == "a human note the QP has no idea about"
    # ...and the QP still fills in what the file did not have.
    assert data["company"]["dba"] == "Lakeside Towing LLC"
    assert len(data["vehicles"]) == 1


def test_qp_only_drop_never_mutates_the_caller_supplied_dossier(monkeypatch):
    # merge_into_dossier does not deep-copy its base (see qp_read.py); the
    # fast path must guard against handing it the caller's own object.
    monkeypatch.setattr(process_drop, "claude_run", _forbidden_claude_run)
    existing = {"_identifier_notes": ["pre-existing note"]}
    before = json.dumps(existing, sort_keys=True)

    process_drop.claude_extract("", [str(REAL_QP)], existing)

    assert json.dumps(existing, sort_keys=True) == before


def test_qp_warnings_and_merge_conflicts_reach_the_caller_as_notes(monkeypatch):
    """qp_read's own warnings (FEIN dot-format) and merge_into_dossier's
    conflicts (stated value mismatch) must travel out through the same
    _identifier_notes channel claude_extract already uses for every other
    note — that is how they reach the Slack thread (see condense_notes /
    triage_flags in process_drop.py)."""
    monkeypatch.setattr(process_drop, "claude_run", _forbidden_claude_run)
    existing = {"coverages": {"total_stated_value": 55000}}

    data = process_drop.claude_extract("", [str(REAL_QP)], existing)

    notes = data.get("_identifier_notes") or []
    assert any("85.0604178" in n for n in notes), "qp_read's own FEIN warning must surface"
    assert any("55000" in n and "50000" in n for n in notes), \
        "the stated-value disagreement between the file and the QP must surface"


# ---------------------------------------------------------------- QP + instruction: model still runs


def test_qp_with_instruction_runs_the_model_and_the_fresh_value_wins(monkeypatch):
    fake = FakeClaudeRun({
        "company": {"fein": "11-1111111"},          # the "instructed" value
        "vehicles": [], "_op": "create", "_summary": "test", "red_flags": [],
    })
    monkeypatch.setattr(process_drop, "claude_run", fake)

    data = process_drop.claude_extract(
        "the FEIN should be 11-1111111", [str(REAL_QP)], None)

    assert fake.call_count >= 1, "an instruction alongside a QP must still reach the model"
    # the freshly-extracted/instructed value wins over the QP's own snapshot...
    assert data["company"]["fein"] == "11-1111111"
    # ...but the QP still fills in whatever the fresh read did not answer.
    assert data["company"]["owner_name"] == "Salomon Lakeside"
    assert len(data["vehicles"]) == 1
    # and the disagreement itself is not silently dropped either.
    notes = data.get("_identifier_notes") or []
    assert any("fein" in n.lower() for n in notes)


def test_qp_plus_another_file_without_instruction_still_runs_the_model(monkeypatch, tmp_path):
    fake = FakeClaudeRun({
        "company": {"dba": "Something Else LLC"},
        "vehicles": [], "_op": "add", "_summary": "test", "red_flags": [],
    })
    monkeypatch.setattr(process_drop, "claude_run", fake)
    note_file = tmp_path / "note.txt"
    note_file.write_text("extra note from the broker", encoding="utf-8")

    data = process_drop.claude_extract("", [str(REAL_QP), str(note_file)], None)

    assert fake.call_count >= 1, "another attachment must still reach the model"
    assert len(data["vehicles"]) == 1          # gap-filled from the QP


# ---------------------------------------------------------------- non-QP / corrupt files: untouched, no crash


def test_non_qp_pdf_is_left_for_the_model_untouched(monkeypatch, tmp_path):
    import fitz
    doc = fitz.open()
    doc.new_page()
    blank = tmp_path / "loss_run_scan.pdf"
    doc.save(str(blank))
    doc.close()

    fake = FakeClaudeRun({
        "company": {"dba": "From The Model"}, "vehicles": [],
        "_op": "create", "_summary": "test", "red_flags": [],
    })
    monkeypatch.setattr(process_drop, "claude_run", fake)

    data = process_drop.claude_extract("", [str(blank)], None)

    assert fake.call_count >= 1, "a PDF with no QP shape must still reach the model"
    assert data["company"]["dba"] == "From The Model"


def test_corrupt_pdf_falls_back_to_the_model_without_crashing(monkeypatch, tmp_path):
    fake_pdf = tmp_path / "fake.pdf"
    fake_pdf.write_text("this is not a PDF file", encoding="utf-8")

    fake_run = FakeClaudeRun({
        "company": {"dba": "From The Model"}, "vehicles": [],
        "_op": "create", "_summary": "test", "red_flags": [],
    })
    monkeypatch.setattr(process_drop, "claude_run", fake_run)

    data = process_drop.claude_extract("", [str(fake_pdf)], None)

    assert fake_run.call_count >= 1
    assert data["company"]["dba"] == "From The Model"
