"""A capture must prove it holds real content before it can become a QP page.

On 2026-07-29 a Yelp "You have been blocked." page was captured and turned into
a page of a real client's quoting packet. The page text read fine when checked —
Yelp served the profile to the DOM and the block to the screenshot, so the two
disagreed. Only the image was wrong, and nothing looked at the image.

Two independent signals, both required:
  * the page text must not carry a block/error marker, and must mention the client
  * the image must carry enough ink to be a real page (a block page is ~2% ink,
    the real captures that day measured 6.4%, 6.5% and 22.7%)
"""
from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

import pagebuild


def blank_png(tmp_path: Path, name="blank.png", size=(800, 600)) -> Path:
    p = tmp_path / name
    Image.new("RGB", size, "white").save(p)
    return p


def dense_png(tmp_path: Path, name="dense.png", size=(800, 600)) -> Path:
    """Half black — unambiguously a page with content on it."""
    im = Image.new("RGB", size, "white")
    for y in range(size[1] // 2):
        for x in range(size[0]):
            im.putpixel((x, y), (0, 0, 0))
    p = tmp_path / name
    im.save(p)
    return p


def test_ink_coverage_tells_a_blank_page_from_a_full_one(tmp_path):
    assert pagebuild.ink_coverage(blank_png(tmp_path)) < 0.01
    assert pagebuild.ink_coverage(dense_png(tmp_path)) > 0.4


def test_a_block_page_is_refused_by_its_text(tmp_path):
    with pytest.raises(pagebuild.CaptureRejected) as e:
        pagebuild.capture_to_pdf(
            dense_png(tmp_path), tmp_path / "out.pdf", "title", "attr",
            page_text="Yelp\nYou have been blocked.\nWhy? Something about the behaviour",
            must_mention="Brookfield")
    assert "blocked" in str(e.value).lower()
    assert not (tmp_path / "out.pdf").exists(), "nothing may be written on rejection"


def test_a_capture_that_never_names_the_client_is_refused(tmp_path):
    with pytest.raises(pagebuild.CaptureRejected):
        pagebuild.capture_to_pdf(
            dense_png(tmp_path), tmp_path / "out.pdf", "title", "attr",
            page_text="Top 10 best towing near Carmichael. Maverik. Addi's Towing.",
            must_mention="Brookfield")


def test_a_near_blank_image_is_refused_even_when_the_text_looks_fine(tmp_path):
    # The Yelp case exactly: DOM said profile, image said blocked.
    with pytest.raises(pagebuild.CaptureRejected) as e:
        pagebuild.capture_to_pdf(
            blank_png(tmp_path), tmp_path / "out.pdf", "title", "attr",
            page_text="Brookfield Towing 4.8 (34 reviews) Claimed Towing Roadside",
            must_mention="Brookfield")
    assert "ink" in str(e.value).lower() or "content" in str(e.value).lower()


def test_a_good_capture_is_written(tmp_path):
    n = pagebuild.capture_to_pdf(
        dense_png(tmp_path), tmp_path / "out.pdf", "title", "attr",
        page_text="Brookfield Towing 4.8 (34 reviews) Claimed Towing Roadside",
        must_mention="Brookfield")
    assert n >= 1
    assert (tmp_path / "out.pdf").exists()


def test_unchecked_captures_still_work_but_only_without_a_claim(tmp_path):
    # Existing callers pass no page_text; they keep working so this change cannot
    # break the pipeline mid-flight. The guard engages as soon as a claim is made.
    n = pagebuild.capture_to_pdf(dense_png(tmp_path), tmp_path / "out.pdf", "t", "a")
    assert n >= 1
