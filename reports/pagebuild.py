r"""Turn a screenshot into report pages that look like JC's.

His report pages are deliberately plain. Read straight off page 29 of
`LAKES CAP tow QP 6.10.26.pdf`:

  * a small centered black title — just the address or entity, nothing else
  * the capture itself, full width inside a modest margin, with the source's
    own chrome left intact (Google's date card, the "Google Maps" watermark)
  * a small right-aligned grey attribution line under the image

No Sierra Pacific letterhead, no coloured banner, no "retrieved at" stamp. The
letterhead belongs to the Sierra forms (app, Meta data, QP Checklist); a report
page is evidence, and evidence is worth more when it looks untouched.

A capture taller than one page is sliced across pages: the title repeats, the
attribution only prints under the last slice, so the report reads as one
continuous document rather than N labelled fragments.
"""
from __future__ import annotations

from pathlib import Path

import fitz
from PIL import Image

LETTER = fitz.paper_rect("letter")          # 612 x 792 pt
MARGIN = 28.0                               # matches the real pages: img @ x=28
TITLE_Y = 82.0                              # baseline of the centered title
IMG_TOP = 108.0                             # first pixel row of the capture
BOTTOM = 60.0                               # room for the attribution line
INK = (0.0, 0.0, 0.0)
GREY = (0.42, 0.42, 0.42)


def _title(page: fitz.Page, text: str) -> None:
    if not text:
        return
    size = 11.5
    w = fitz.get_text_length(text, fontname="helv", fontsize=size)
    page.insert_text((LETTER.width / 2 - w / 2, TITLE_Y), text,
                     fontname="helv", fontsize=size, color=INK)


def _attribution(page: fitz.Page, text: str, y: float) -> None:
    if not text:
        return
    size = 7.5
    w = fitz.get_text_length(text, fontname="helv", fontsize=size)
    page.insert_text((LETTER.width - MARGIN - w, min(y + 14, LETTER.height - 22)),
                     text, fontname="helv", fontsize=size, color=GREY)


class CaptureRejected(Exception):
    """The capture does not hold what it claims to, so it is not written.

    A missing page in a quoting packet is a gap the completion gate reports. A
    page that LOOKS like evidence but is a bot wall is worse: it reaches an
    underwriter as if it were the real record.
    """


# Seen on real bot walls and error pages served to this pipeline.
BLOCK_MARKERS = (
    "you have been blocked", "access denied", "are you a robot",
    "unusual traffic", "verify you are human", "captcha",
    "enable javascript", "could not be retrieved", "403 forbidden",
    "rate limit", "too many requests", "temporarily unavailable",
)

# A block page is nearly empty. Measured on 2026-07-29: the Yelp block page came
# in at 2.0% ink, while the three genuine captures that day were 6.4% (CHP),
# 6.5% (SAFER) and 22.7% (a company website). 4% sits between them with room on
# both sides; a legitimately sparse page should declare itself via page_text.
MIN_INK = 0.04


def ink_coverage(png: Path, step: int = 3) -> float:
    """Fraction of sampled pixels that are not near-white."""
    with Image.open(png) as im:
        g = im.convert("L")
        px = g.load()
        w, h = g.size
        dark = total = 0
        for y in range(0, h, step):
            for x in range(0, w, step):
                total += 1
                if px[x, y] < 200:
                    dark += 1
    return (dark / total) if total else 0.0


def verify_capture(png: Path, page_text: str, must_mention: str = "") -> None:
    """Raise CaptureRejected unless this capture can be trusted as evidence.

    Two independent signals, because neither alone is enough. The text and the
    image are separate fetches: Yelp served a real profile to the DOM and a
    block page to the screenshot in the same session, so a text-only check
    passed while the image was worthless.
    """
    low = (page_text or "").lower()
    hit = next((m for m in BLOCK_MARKERS if m in low), None)
    if hit:
        raise CaptureRejected(f"page text carries a block marker: {hit!r}")

    if must_mention and must_mention.lower() not in low:
        raise CaptureRejected(
            f"the capture never mentions {must_mention!r} — a search-results page "
            f"or the wrong business, not this client's record")

    ink = ink_coverage(png)
    if ink < MIN_INK:
        raise CaptureRejected(
            f"image is {ink:.1%} ink, below the {MIN_INK:.0%} floor — it reads as a "
            f"blank or block page even though the text looked fine")


def capture_to_pdf(png: Path, pdf_path: Path, title: str,
                   attribution: str = "", page_text: str | None = None,
                   must_mention: str = "") -> int:
    """Slice `png` across letter pages. Returns the page count written.

    Pass `page_text` (what the page actually said) and `must_mention` (the client
    name) to have the capture verified before anything is written. Callers that
    pass neither keep the old behaviour, so this cannot break the pipeline
    mid-flight — but anything destined for a client packet should verify.
    """
    if page_text is not None:
        verify_capture(png, page_text, must_mention)
    avail_w = LETTER.width - 2 * MARGIN
    avail_h = LETTER.height - IMG_TOP - BOTTOM

    with Image.open(png) as im:
        im = im.convert("RGB")
        scale = avail_w / im.width
        # how many source pixels fit on one page
        slice_px = max(1, int(avail_h / scale))
        slices = [(t, min(t + slice_px, im.height))
                  for t in range(0, im.height, slice_px)]
        # a final sliver shorter than ~8% of a page reads as a printing error;
        # fold it into the previous slice instead
        if len(slices) > 1 and (slices[-1][1] - slices[-1][0]) < slice_px * 0.08:
            slices[-2] = (slices[-2][0], slices[-1][1])
            slices.pop()

        out = fitz.open()
        for i, (top, bot) in enumerate(slices):
            part = im.crop((0, top, im.width, bot))
            page = out.new_page(width=LETTER.width, height=LETTER.height)
            _title(page, title)
            h = part.height * scale
            rect = fitz.Rect(MARGIN, IMG_TOP, MARGIN + avail_w, IMG_TOP + h)
            buf = pdf_path.parent / f".{pdf_path.stem}_{i}.png"
            part.save(buf)
            page.insert_image(rect, filename=str(buf))
            buf.unlink(missing_ok=True)
            if i == len(slices) - 1:
                _attribution(page, attribution, rect.y1)
        pdf_path.parent.mkdir(parents=True, exist_ok=True)
        out.save(pdf_path)
        n = len(out)
        out.close()
    return n


def merge_to_pdf(items: list[tuple[Path, str, str]], pdf_path: Path) -> int:
    """Several captures into one report, each starting on its own page.

    Used where JC puts two sources on one sheet (Facebook and Instagram share
    a page in the real packet) — the caller decides the grouping, this only
    concatenates without re-labelling.
    """
    out = fitz.open()
    for png, title, attrib in items:
        tmp = pdf_path.parent / f".{pdf_path.stem}_{png.stem}.pdf"
        capture_to_pdf(png, tmp, title, attrib)
        with fitz.open(tmp) as d:
            out.insert_pdf(d)
        tmp.unlink(missing_ok=True)
    pdf_path.parent.mkdir(parents=True, exist_ok=True)
    out.save(pdf_path)
    n = len(out)
    out.close()
    return n
