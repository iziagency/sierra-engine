"""A rebuild is not a clean bill of health.

`Prep SUMMI1 CAP app` on 8.1.26 answered with a green check, a Drive link and
the Notion stamp. The file it had just rebuilt carried FOURTEEN unresolved red
flags, a carrier last confirmed in 2025 and an expiration 76 days in the past.
None of it was mentioned.

That is the same failure the drop reply was designed to avoid, one level up: a
document is only as finished as its open questions, and a broker who sees a
check mark ships it. Prep rebuilds; it does not resolve anything, so whatever
was open before is still open and has to be said.
"""
from __future__ import annotations

import json

import slack_engine as se


def dossier_with(flags, **company):
    return {"sp_code": "SUMMI1",
            "company": {"first_named_insured": "Maximum Towing LLC", **company},
            "_red_flags": flags}


def test_open_flags_are_counted_in_the_reply():
    line = se.open_questions_line(dossier_with(["a", "b", "c"]))
    assert "3" in line
    assert "still open" in line.lower()


def test_the_first_few_are_named_not_just_counted():
    line = se.open_questions_line(dossier_with([
        "Home based answered No on page 1 but Yes on page 7",
        "Total commercial locations declared as 2, only 1 scheduled",
    ]))
    assert "Home based" in line


def test_a_clean_file_says_so_without_noise():
    assert se.open_questions_line(dossier_with([])) == ""


def test_the_reply_for_a_rebuilt_app_carries_them(tmp_path, monkeypatch):
    slug = "maximum-towing"
    root = tmp_path / "app-form" / "clients" / slug
    root.mkdir(parents=True)
    (root / "state.json").write_text(json.dumps(dossier_with(
        ["coverage may have lapsed — what is in force today?"])),
        encoding="utf-8")
    monkeypatch.setattr(se, "HERE", tmp_path / "watcher")

    text = se.rebuilt_app_reply(slug, "SUMMI1 CAP app 8.1.26.pdf",
                                "https://drive.google.com/x")
    assert "SUMMI1 CAP app 8.1.26.pdf" in text
    assert "Notion stamp" in text
    assert "1 question still open" in text
    assert "lapsed" in text


def test_a_clean_rebuild_reads_clean(tmp_path, monkeypatch):
    slug = "spotless-towing"
    root = tmp_path / "app-form" / "clients" / slug
    root.mkdir(parents=True)
    (root / "state.json").write_text(json.dumps(dossier_with([])), encoding="utf-8")
    monkeypatch.setattr(se, "HERE", tmp_path / "watcher")

    text = se.rebuilt_app_reply(slug, "SPOT1 CAP app 8.1.26.pdf", "https://x")
    assert "still open" not in text
    assert "Notion stamp" in text
