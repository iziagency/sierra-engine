"""One client must never end up with two half-finished dossiers.

Nora's Towing was dropped twice. The second drop extracted a slightly different
company name, the local folder name is derived from that name, and the dossier
forked: `nora-s-towing-inc` kept the filled app, `a-s-towing` kept the changelog,
the audit PDF and the archived sources. The Drive upload then globbed for the
app inside the second folder, found nothing, and silently skipped the one file
JC actually wants.

The Drive side already solved this — drive_api anchors the folder on the SP
code, with a comment recording that one client once collected three Drive
folders in a day. The local side never got the same treatment.

The SP code is derived from the name too, but it is stable across the spelling
wobble that breaks the slug: both readings of this client gave NORAS1.
"""
from __future__ import annotations

import json

import process_drop as pd


def make_dossier(tmp_path, slug: str, sp_code: str, **extra):
    folder = tmp_path / slug
    folder.mkdir(parents=True)
    (folder / "state.json").write_text(
        json.dumps({"sp_code": sp_code, "company": {"first_named_insured": "x"}, **extra}),
        encoding="utf-8")
    return folder


def test_finds_the_existing_folder_by_sp_code(tmp_path, monkeypatch):
    monkeypatch.setattr(pd, "CLIENTS", tmp_path)
    make_dossier(tmp_path, "nora-s-towing-inc", "NORAS1")
    assert pd.slug_for_sp_code("NORAS1") == "nora-s-towing-inc"


def test_returns_nothing_for_an_unknown_code(tmp_path, monkeypatch):
    monkeypatch.setattr(pd, "CLIENTS", tmp_path)
    make_dossier(tmp_path, "nora-s-towing-inc", "NORAS1")
    assert pd.slug_for_sp_code("NEWCO") == ""


def test_a_blank_code_never_matches(tmp_path, monkeypatch):
    monkeypatch.setattr(pd, "CLIENTS", tmp_path)
    make_dossier(tmp_path, "no-code-here", "")
    assert pd.slug_for_sp_code("") == ""


def test_prefers_the_oldest_folder_when_the_fork_already_happened(tmp_path, monkeypatch):
    # Both halves of the Nora's fork carry NORAS1. The original is the one to
    # keep working in; picking arbitrarily would just move the split around.
    monkeypatch.setattr(pd, "CLIENTS", tmp_path)
    first = make_dossier(tmp_path, "nora-s-towing-inc", "NORAS1")
    second = make_dossier(tmp_path, "a-s-towing", "NORAS1")
    import os
    os.utime(first / "state.json", (1, 1))
    os.utime(second / "state.json", (2000, 2000))
    assert pd.slug_for_sp_code("NORAS1") == "nora-s-towing-inc"


def test_a_dossier_without_state_json_is_skipped(tmp_path, monkeypatch):
    monkeypatch.setattr(pd, "CLIENTS", tmp_path)
    (tmp_path / "empty-folder").mkdir()
    make_dossier(tmp_path, "nora-s-towing-inc", "NORAS1")
    assert pd.slug_for_sp_code("NORAS1") == "nora-s-towing-inc"


def test_unreadable_state_json_does_not_crash_the_lookup(tmp_path, monkeypatch):
    monkeypatch.setattr(pd, "CLIENTS", tmp_path)
    bad = tmp_path / "broken"
    bad.mkdir()
    (bad / "state.json").write_text("{not json", encoding="utf-8")
    make_dossier(tmp_path, "nora-s-towing-inc", "NORAS1")
    assert pd.slug_for_sp_code("NORAS1") == "nora-s-towing-inc"
