"""An audit entry must name the document it came from — even a re-sent one.

FALCO1, 8.1.26: an email screenshot already archived on 7.27 was dropped again.
The dedupe correctly refused to store a second copy of identical bytes, and the
changelog entry then read "add · 4 changes" with an EMPTY source field. The
stated value of three trucks moved and nothing on the record said which
document moved them.

JC's Change History exists to answer who / when / what / how. Which paper the
change came off is part of "how", and "we already had this file" is not a
reason to drop it from the record — it is the record.
"""
from __future__ import annotations

import process_drop as pd


def drop(tmp_path, name: str, content: bytes = b"x" * 100):
    f = tmp_path / name
    f.write_bytes(content)
    return str(f)


def test_a_new_file_is_archived_and_named(tmp_path):
    src = tmp_path / "_source"
    src.mkdir()
    f = drop(tmp_path, "1754087345.1_loss_run.pdf")

    archived, upload, reused = pd.archive_sources([f], src, "20260801_2029")
    assert archived == ["20260801_2029_loss_run.pdf"]
    assert [str(p) for p in upload] == [f]      # the original, for the Drive copy
    assert reused == []
    assert (src / "20260801_2029_loss_run.pdf").exists()


def test_a_resent_file_is_not_copied_but_IS_credited(tmp_path):
    src = tmp_path / "_source"
    src.mkdir()
    (src / "20260727_1736_falcon_ridge_email.png").write_bytes(b"x" * 100)
    f = drop(tmp_path, "1754087345.1_falcon_ridge_email.png")

    archived, upload, reused = pd.archive_sources([f], src, "20260801_2029")
    assert archived == []                       # no second copy of the bytes
    assert upload == []                         # nothing to re-upload
    assert reused == ["falcon_ridge_email.png (already on file)"]
    assert len(list(src.iterdir())) == 1        # still exactly one copy


def test_the_same_name_with_different_bytes_is_a_revision_and_is_kept(tmp_path):
    src = tmp_path / "_source"
    src.mkdir()
    (src / "20260727_1736_app.pdf").write_bytes(b"x" * 100)
    f = drop(tmp_path, "1754087345.1_app.pdf", b"y" * 250)

    archived, _, reused = pd.archive_sources([f], src, "20260801_2029")
    assert archived == ["20260801_2029_app.pdf"]
    assert reused == []


def test_a_missing_file_is_skipped_quietly(tmp_path):
    src = tmp_path / "_source"
    src.mkdir()
    archived, upload, reused = pd.archive_sources(
        [str(tmp_path / "gone.pdf")], src, "20260801_2029")
    assert (archived, upload, reused) == ([], [], [])


def test_the_source_line_credits_both_new_and_reused():
    line = pd.source_line(["20260801_2029_loss_run.pdf"],
                          ["falcon_ridge_email.png (already on file)"])
    assert "loss_run.pdf" in line
    assert "falcon_ridge_email.png (already on file)" in line


def test_a_drop_that_carried_nothing_says_nothing():
    assert pd.source_line([], []) == ""
