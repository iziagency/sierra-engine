r"""The page that says what the engine noticed.

Everything else in the quoting packet is evidence: the filled application, the
DMV pull, the loss runs, the Yelp profile. This page is the reading of that
evidence, and it is the only part of the packet a broker could not have produced
by collecting documents.

It exists because the findings were being thrown away. Lakeside's Statement of
Information has been overdue since 03/31/2022, his Yelp listing says open 24
hours while the application declares under twelve, and the owner's cousin drives
the Hino to calls without being on the policy. The engine found all three, said
each of them once in Slack, and the 41-page packet that went out mentioned none
of them. A finding nobody can act on later is a finding that was never made.

Deliberately plain, like JC's report pages: a centered title, black text, no
letterhead, no colour. Two groups, in the order a human needs them — what has to
be answered before a carrier sees this, then what was read but not verified.
"""
from __future__ import annotations

import datetime
import re
import sys
from pathlib import Path

import fitz

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "shared"))
import formatting  # noqa: E402 - broker-facing dates obey JC's M.D.YY shape

LETTER = fitz.paper_rect("letter")
MARGIN = 54.0
TITLE_Y = 82.0
BODY_TOP = 118.0
LINE = 13.2
SIZE = 9.5
INK = (0.0, 0.0, 0.0)
GREY = (0.42, 0.42, 0.42)

# A note that only describes how the engine read a document is not a finding. The
# same list is filtered in the Slack reply; here it keeps the packet from opening
# with "six photographs are not square to the form".
HOUSEKEEPING = (
    "square to the form", "yes/no boxes", "identifiers came from per-image reads",
    "unpacked", "attachment(s) from the email", "was read on its own",
    "so it was applied as an instruction", "crop re-read unavailable",
    "recovered by a focused re-read", "did not come through the general reading",
)

# Findings that stop a quote, in the order an underwriter would want them.
BLOCKING = (
    "no legible vin", "fails its check digit", "left blank",
    "not on the policy", "not listed as a driver", "undisclosed", "never filed",
    "say which is right", "overdue", "conflict", "suspended", "expired",
    "not on payroll", "no vin", "unlisted",
)


def _ascii(text: str) -> str:
    """Characters the base PDF fonts can actually draw.

    Helvetica's built-in encoding has no em dash and no curly quotes, so PyMuPDF
    silently substituted a middle dot: the page read `first reading ·N/A·, second
    reading ·illegible·`, which looks like corruption on a document going to a
    carrier. Embedding a Unicode font for two punctuation marks is not worth it —
    the ASCII spellings say the same thing.
    """
    for bad, good in (("—", " - "), ("–", "-"), ("‘", "'"),
                      ("’", "'"), ("“", '"'), ("”", '"'),
                      ("…", "..."), ("·", "-"), (" ", " ")):
        text = text.replace(bad, good)
    return " ".join(text.split())


def _plain(text: str) -> str:
    """Field paths into words. `ops_details.gross_revenue` is how the engine
    stores it; `gross revenue` is what the person reading the packet calls it."""
    out = _ascii(text)
    m = re.match(r"^([a-z_]+)\.([a-z_0-9]+)\s*:\s*(.*)$", out)
    if m:
        return f"{m.group(2).replace('_', ' ')}: {m.group(3)}"
    m = re.match(r"^([a-z_]+)\[(\d+)\]\.([a-z_0-9]+)\s*:\s*(.*)$", out)
    if m:
        return (f"{m.group(1)[:-1]} {int(m.group(2)) + 1} "
                f"{m.group(3).replace('_', ' ')}: {m.group(4)}")
    return out


def _wrap(text: str, width: float, size: float = SIZE) -> list[str]:
    """Greedy wrap by measured width, because a finding is a sentence, not a cell."""
    words, lines, cur = str(text).split(), [], ""
    for w in words:
        trial = f"{cur} {w}".strip()
        if fitz.get_text_length(trial, fontname="helv", fontsize=size) <= width:
            cur = trial
        else:
            if cur:
                lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines or [""]


def classify(notes: list[str], flags: list[str]) -> tuple[list[str], list[str]]:
    """Split everything on file into (must be answered, read but unverified)."""
    seen, blocking, context = set(), [], []
    for item in list(flags) + list(notes):
        one = _plain(item)
        if not one or one in seen:
            continue
        seen.add(one)
        low = one.lower()
        if any(h in low for h in HOUSEKEEPING):
            continue
        # case-insensitive: the conflict notes shout "NOT changed", and matching
        # the lowercase spelling filed a $250,000 revenue discrepancy under
        # "read but not verified" instead of "answer before a carrier sees this"
        (blocking if any(b in low for b in BLOCKING) else context).append(one)
    return blocking, context


def build(dossier: dict, pdf_path: Path, sp: str, client: str) -> int:
    """Write the findings page(s). Returns the page count, 0 when nothing to say."""
    blocking, context = classify(dossier.get("_identifier_notes") or [],
                                dossier.get("_red_flags") or [])
    if not blocking and not context:
        return 0

    doc = fitz.open()
    page = doc.new_page(width=LETTER.width, height=LETTER.height)
    title = _ascii(f"{sp} - {client} - findings and open questions")
    tw = fitz.get_text_length(title, fontname="helv", fontsize=11.5)
    page.insert_text((LETTER.width / 2 - tw / 2, TITLE_Y), title,
                     fontname="helv", fontsize=11.5, color=INK)

    body_w = LETTER.width - 2 * MARGIN
    y = BODY_TOP

    def new_page() -> None:
        nonlocal page, y
        page = doc.new_page(width=LETTER.width, height=LETTER.height)
        y = BODY_TOP

    def heading(text: str) -> None:
        nonlocal y
        if y + LINE * 3 > LETTER.height - 70:
            new_page()
        y += 6
        page.insert_text((MARGIN, y), text, fontname="hebo", fontsize=10, color=INK)
        y += LINE + 2

    def item(n: int, text: str) -> None:
        nonlocal y
        lines = _wrap(text, body_w - 18)
        if y + LINE * len(lines) > LETTER.height - 70:
            new_page()
        page.insert_text((MARGIN, y), f"{n}.", fontname="helv", fontsize=SIZE,
                         color=INK)
        for i, ln in enumerate(lines):
            page.insert_text((MARGIN + 18, y + i * LINE), ln,
                             fontname="helv", fontsize=SIZE, color=INK)
        y += LINE * len(lines) + 3

    if blocking:
        heading("Answer before this goes to a carrier")
        for i, f in enumerate(blocking, 1):
            item(i, f)
    if context:
        heading("Read from the file, not independently verified")
        for i, f in enumerate(context, 1):
            item(i, f)

    # Two lines, both drawn. The first version rendered only the first wrapped
    # line and the footer stopped mid-sentence at "left blank on purpose: no".
    foot = _ascii(
        f"Generated by the Sierra intake engine on "
        f"{formatting.format_date(datetime.date.today())}. Every item traces to an "
        f"entry in this client's change log. Nothing here was guessed: a field "
        f"the engine could not read with confidence was left blank on purpose.")
    fl = _wrap(foot, body_w, 7.5)
    base = LETTER.height - 46 - (len(fl) - 1) * 9.5
    for i, ln in enumerate(fl):
        page.insert_text((MARGIN, base + i * 9.5), ln, fontname="helv",
                         fontsize=7.5, color=GREY)
    doc.save(pdf_path)
    n = len(doc)
    doc.close()
    return n
