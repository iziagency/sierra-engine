r"""Read identifiers from magnified crops of their own cell.

Why this exists, measured rather than assumed. Asked to transcribe the Borderline
Recovery fixture from the whole page, the reader returned:

    FEIN       82-5566771  ->  83-3766771, then 43-3766771, then 63-3366771
    cell       760-555-0193 ->  760-555-0543, stable across runs
    3 VINs                  ->  all three correct

Handed a 5x crop of the phone cell alone, it returned 760-555-0193 three times
out of three. The failure is not capability and not randomness: on a full page
those digits survive image encoding at roughly a dozen pixels tall, which is
enough signal to reconstruct a patterned string like `760-555-` and not enough
for the four digits that carry actual information. The VINs came through because
they are written larger and sit behind a `VIN` label that anchors them.

So identifiers are read from their own cell, magnified. The template already
knows where every cell is — it is the same form the client filled in — so the
widget rectangles map onto the scan by a single scale factor.

This holds only while the scan is square to the page, which is true of a scanner
or a PDF export and false of a hand-held photo. `page_scale` returns None when
the image does not match the page's aspect ratio, and the caller falls back to
the double-read in process_drop.py rather than cropping the wrong region — a
confident reading of the wrong rectangle is worse than no reading at all.
"""
from __future__ import annotations

import concurrent.futures as cf
import json
import re
import subprocess
import time
from pathlib import Path

import fitz
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = ROOT / "app-form" / "dist" / "CAP_app_2026_fillable.pdf"
FIELD_MAP = ROOT / "app-form" / "dist" / "field_map.json"

ZOOM = 5                    # 5x was where the phone cell became reliable
# The crop pass is one model call per field, so left unbounded it can outlast the
# whole pipeline: ten fields on page 1 plus twenty vehicle rows would blow a
# five-minute ceiling on its own. It gets a slice of the budget and, when the
# slice runs out, says which fields it did not get to instead of running long.
BUDGET_SECONDS = 130
CALL_TIMEOUT = 45
# Four concurrent readers, not eight: enough to collapse the wall clock
# without a burst large enough to trip API rate limits.
MAX_PARALLEL = 4
PAD = 2.0                   # pt of slack around the cell, for ink that overruns
ASPECT_TOLERANCE = 0.02     # a photo is never this square to the page

# template field name -> dossier path. Only fields whose value is pure entropy:
# no linguistic pattern, no checksum this reader can verify, every character
# equally likely. Names and addresses are deliberately absent — they carry enough
# redundancy that the whole-page read handles them.
ID_FIELDS = {
    "p1_fein_or_social_security_number": "company.fein",
    "p1_us_dot_number": "company.usdot_number",
    "p1_mc_number": "company.mc_number",
    "p1_state_filing_number": "company.state_filing_number",
    "p1_contact_cell_phone": "company.contact_cell",
    "p1_office_phone": "company.office_phone",
}
# vehicle rows share one cell for stated value and VIN
VEH_FIELD = "p5_veh{n:02d}_value_vin"

READ_PROMPT = """Transcribe what is handwritten in this image, character by character.

Rules:
- Report only what you can actually see. Do not complete a pattern, do not use
  what the value "should" look like, do not correct anything.
- If any character is not clearly legible, reply exactly: ILLEGIBLE
- Reply with the transcription alone. No explanation, no label, no punctuation
  you did not see in the image.
"""


def page_scale(img: Image.Image, page: fitz.Page) -> float | None:
    """Pixels per point, or None if this image is not a square scan of the page."""
    sx = img.width / page.rect.width
    sy = img.height / page.rect.height
    if not sx or abs(sx - sy) / sx > ASPECT_TOLERANCE:
        return None
    return sx


def widget_rects(page_no: int) -> dict[str, fitz.Rect]:
    """field name -> rect, for one page of the fillable template."""
    with fitz.open(TEMPLATE) as doc:
        if page_no > len(doc):
            return {}
        return {w.field_name: fitz.Rect(w.rect) for w in doc[page_no - 1].widgets()}


def crop_field(img: Image.Image, rect: fitz.Rect, scale: float, dest: Path) -> bool:
    box = (max(0, int((rect.x0 - PAD) * scale)),
           max(0, int((rect.y0 - PAD) * scale)),
           min(img.width, int((rect.x1 + PAD) * scale)),
           min(img.height, int((rect.y1 + PAD) * scale)))
    if box[2] - box[0] < 12 or box[3] - box[1] < 6:
        return False
    part = img.crop(box)
    part = part.resize((part.width * ZOOM, part.height * ZOOM), Image.LANCZOS)
    dest.parent.mkdir(parents=True, exist_ok=True)
    part.save(dest)
    return True


def read_crop(png: Path, claude: str) -> str:
    try:
        return _read_crop(png, claude)
    except subprocess.TimeoutExpired:
        return ""


def _read_crop(png: Path, claude: str) -> str:
    proc = subprocess.run(
        [claude, "-p", "--output-format", "text", "--allowedTools", "Read",
         "--model", "claude-sonnet-5"],
        input=READ_PROMPT + "\n\n" + str(png),
        capture_output=True, text=True, timeout=CALL_TIMEOUT, shell=False,
        encoding="utf-8", errors="replace")
    if proc.returncode != 0:
        return ""
    out = " ".join(proc.stdout.split())
    return "" if out.upper().startswith("ILLEGIBLE") else out


def _vin_of(text: str) -> str:
    """Pull the VIN out of a schedule cell that also holds the stated value.

    A VIN is 17 characters, letters and digits, and never contains I, O or Q —
    the standard excludes them precisely so they cannot be confused with 1 and 0.
    That makes it identifiable inside free text without relying on the `VIN`
    label being transcribed.
    """
    after = re.split(r"\bVIN\b", text, maxsplit=1, flags=re.I)
    hay = after[1] if len(after) > 1 else text
    for token in re.findall(r"[A-Z0-9]{17}", hay.upper().replace(" ", "")):
        if not set("IOQ") & set(token):
            return token
    return ""


def _value_of(text: str) -> int | None:
    """Stated value from the same schedule cell as the VIN.

    The cell reads `62,500  VIN 1FDUF5HT4KEC77219`. The whole-page pass turned
    that 62,500 into 62,300 — the identical failure as the FEIN, a digit invented
    where nothing constrains it — and only the total-versus-list cross-check
    caught it. The magnified crop is already being read for the VIN, so the value
    comes along for free instead of being discarded.
    """
    # Cut the VIN out of the string before looking for money, and take the FIRST
    # number that remains rather than the last. Splitting on the literal word
    # "VIN" was too fragile: when the reader returned "V1N", or the label fell
    # outside the crop, the whole cell survived as the haystack and the last match
    # was the VIN's own tail — which is how a $58,000 Ford ended up on file at
    # 11223, the closing digits of 1FDUF4GT6NEC11223. The stated value is written
    # first in this cell, always.
    body = re.sub(r"\b[A-Z0-9]{17}\b", " ", text.upper())          # drop the VIN
    body = re.sub(r"\bV[I1L]N\b", " ", body)                       # and its label
    nums = re.findall(r"\d[\d,]*", body.replace("$", ""))
    for n in nums:                       # first plausible amount wins
        try:
            v = int(n.replace(",", ""))
        except ValueError:
            continue
        # A tow unit's stated value lives in the thousands; anything outside that
        # is a row number or a stray mark, not money.
        if 500 <= v <= 5_000_000:
            return v
    return None


def blank_cell(png: Path) -> bool:
    """True when the crop holds no ink — an unanswered field, not a hard read."""
    with Image.open(png) as im:
        g = im.convert("L")
        dark = sum(1 for p in g.getdata() if p < 140)
        return dark < (g.width * g.height) * 0.004


# Yes/no rows on page 1, as (no-box field, yes-box field) -> dossier path. An
# unticked pair is an UNANSWERED question, never a "no". The reader inferred
# `false` for Borderline's dash cameras and telematics from two empty squares
# while correctly leaving Ridgeline's as null — same blank rows, opposite
# conclusions. Telematics is a premium credit, so a fabricated "no" costs the
# client money. Ink is measurable, so measure it instead of asking.
BOOL_ROWS = {
    "company.cross_state_lines": ("p1_cross_state_lines_no", "p1_cross_state_lines_yes"),
    "company.home_based": ("p1_home_based_business_no", "p1_home_based_business_yes"),
    "company.new_venture": ("p1_new_venture_no", "p1_new_venture_yes"),
    "company.dash_cameras": ("p1_vehicle_dash_cameras_no", "p1_vehicle_dash_cameras_yes"),
    "company.telematics": ("p1_vehicle_telematics_no", "p1_vehicle_telematics_yes"),
}
# Sample only the middle of the square. The printed border is itself dark enough
# to read as ink — measured across both fixtures, a full-square sample gives 0.19
# to 0.21 for an EMPTY box and 0.32 to 0.36 for a ticked one, which is far too
# close to separate reliably. Discarding the outer 30% drops empty boxes to
# exactly 0.000 while a tick still reads 0.286 or more, because an X crosses the
# centre and an outline never does. The threshold sits in that gap, nearer the
# floor so speckle on a real scan cannot push an empty box over it.
BOX_INSET = 0.30
BOX_INK = 0.06


def _box_has_ink(img: Image.Image, rect: fitz.Rect, scale: float) -> bool | None:
    w, h = rect.width, rect.height
    inner = fitz.Rect(rect.x0 + w * BOX_INSET, rect.y0 + h * BOX_INSET,
                      rect.x1 - w * BOX_INSET, rect.y1 - h * BOX_INSET)
    x0, y0 = max(0, int(inner.x0 * scale)), max(0, int(inner.y0 * scale))
    x1 = min(img.width, max(int(inner.x1 * scale), x0 + 1))
    y1 = min(img.height, max(int(inner.y1 * scale), y0 + 1))
    if x1 - x0 < 2 or y1 - y0 < 2:
        return None
    px = list(img.crop((x0, y0, x1, y1)).convert("L").getdata())
    if not px:
        return None
    return (sum(1 for p in px if p < 130) / len(px)) > BOX_INK


# Pick-one groups: option field -> the value that option means. Same problem as
# the yes/no rows and the same answer. Borderline has LLC plainly ticked and one
# run still returned no entity type at all, which pushed "Company type?" into the
# questions list for a question the client had already answered.
PICK_ONE = {
    "company.entity_type": {"p1_company_sole": "sole_proprietor",
                            "p1_company_corp": "corporation",
                            "p1_company_llc": "llc"},
    "company.language": {"p1_language_english": "english",
                         "p1_language_spanish": "spanish"},
}

# Names are not identifiers — they carry enough redundancy that the whole-page
# read usually lands them — but `Priya Raman` came back as `Priya Roman`, and a
# misspelled named insured is a rejected filing. They are cheap to re-read from
# their own cell, so they are.
NAME_FIELDS = {
    "p1_first_named_insured_on_filing": "company.first_named_insured",
    "p1_doing_business_as_or_dba": "company.dba",
    "p1_owner_name": "company.owner_name",
    "p1_contact_name": "company.contact_name",
}


def read_bool_rows(files: list[str]) -> dict:
    """{'values': {path: True/False/None}, 'notes': [...]} measured from the page.

    Returns None for a row where neither box is ticked and omits the row entirely
    when the image cannot be mapped, so the caller can tell "asked and unanswered"
    apart from "we could not look".
    """
    values: dict[str, object] = {}
    notes: list[str] = []
    with fitz.open(TEMPLATE) as tpl:
        page = tpl[0]
        rects = {w.field_name: fitz.Rect(w.rect) for w in page.widgets()}
        pw, ph = page.rect.width, page.rect.height

    for f in files:
        path = Path(f)
        if path.suffix.lower() not in (".png", ".jpg", ".jpeg", ".webp"):
            continue
        if re.search(r"(_p0?5|page ?5)", path.name, re.I):
            continue                                  # yes/no rows live on page 1
        with Image.open(path) as img:
            img = img.convert("RGB")
            sx, sy = img.width / pw, img.height / ph
            if not sx or abs(sx - sy) / sx > ASPECT_TOLERANCE:
                notes.append(f"{path.name}: not square to the form — yes/no boxes not "
                             f"measured; treat blank answers as unconfirmed")
                continue
            for dpath, (no_f, yes_f) in BOOL_ROWS.items():
                if no_f not in rects or yes_f not in rects:
                    continue
                no_ink = _box_has_ink(img, rects[no_f], sx)
                yes_ink = _box_has_ink(img, rects[yes_f], sx)
                if no_ink is None or yes_ink is None:
                    continue
                if yes_ink and not no_ink:
                    values[dpath] = True
                elif no_ink and not yes_ink:
                    values[dpath] = False
                elif no_ink and yes_ink:
                    values[dpath] = None
                    notes.append(f"{dpath}: BOTH boxes appear marked — ask the insured "
                                 f"which one they meant")
                else:
                    values[dpath] = None              # neither ticked: unanswered

            for dpath, options in PICK_ONE.items():
                ticked = [val for field, val in options.items()
                          if field in rects and _box_has_ink(img, rects[field], sx)]
                if len(ticked) == 1:
                    values[dpath] = ticked[0]
                elif len(ticked) > 1:
                    values[dpath] = None
                    notes.append(f"{dpath}: more than one option is marked "
                                 f"({', '.join(ticked)}) — ask which one applies")
                else:
                    values[dpath] = None
    return {"values": values, "notes": notes}


def read_identifiers(files: list[str], claude: str, scratch: Path) -> dict:
    """{'values': {dossier path: text}, 'notes': [...], 'scanned': [...]}"""
    values: dict[str, str] = {}
    notes: list[str] = []
    scanned: list[str] = []
    started = time.monotonic()
    skipped: list[str] = []

    with fitz.open(TEMPLATE) as tpl:
        p1, p5 = tpl[0], tpl[4]
        r1, r5 = ({w.field_name: fitz.Rect(w.rect) for w in p1.widgets()},
                  {w.field_name: fitz.Rect(w.rect) for w in p5.widgets()})
        geom1 = fitz.Rect(p1.rect)
        geom5 = fitz.Rect(p5.rect)

    for f in files:
        path = Path(f)
        if path.suffix.lower() not in (".png", ".jpg", ".jpeg", ".webp"):
            continue
        with Image.open(path) as img:
            img = img.convert("RGB")
            # Which page of the form is this? Page 1 and page 5 have the same
            # geometry, so the caller's filename is the only cheap signal; fall
            # back to trying page 1's fields and accepting whatever has ink.
            is_p5 = bool(re.search(r"(_p0?5|page ?5|veh)", path.name, re.I))
            rects = r5 if is_p5 else r1
            geom = geom5 if is_p5 else geom1
            scale = page_scale(img, fitz.open(TEMPLATE)[4 if is_p5 else 0])
            if scale is None:
                notes.append(f"{path.name}: not square to the form "
                             f"({img.width}x{img.height}) — identifiers not re-read "
                             f"from crops; treat them as unverified")
                continue
            scanned.append(path.name)

            targets: list[tuple[str, str]] = []
            if is_p5:
                for n in range(1, 21):
                    name = VEH_FIELD.format(n=n)
                    if name in rects:
                        targets.append((name, f"vehicles[{n}].vin"))
            else:
                targets = [(k, v) for k, v in ID_FIELDS.items() if k in rects]
                targets += [(k, v) for k, v in NAME_FIELDS.items() if k in rects]

            # Cut the crops first — that is local pixel work, milliseconds each —
            # then read them concurrently. Every read is independent: none needs
            # another's answer, so waiting for them one at a time bought nothing.
            # Measured, an empty model call costs 5.2s of pure process startup and a
            # crop read costs 7.6s, so two thirds of this stage was a toll paid over
            # and over. Four at a time rather than all of them, because a burst of
            # concurrent calls can hit API rate limits, and a rate-limited read
            # comes back blank — correct, but it looks like a hole in the form.
            pending: list[tuple[str, Path]] = []
            for name, dpath in targets:
                png = scratch / f"crop_{path.stem}_{name}.png"
                if not crop_field(img, rects[name], scale, png):
                    continue
                if blank_cell(png):
                    png.unlink(missing_ok=True)
                    continue
                pending.append((dpath, png))

            reads: dict[str, str] = {}
            with cf.ThreadPoolExecutor(max_workers=MAX_PARALLEL) as pool:
                futures = {pool.submit(read_crop, png, claude): (dpath, png)
                           for dpath, png in pending}
                for fut in cf.as_completed(futures):
                    dpath, png = futures[fut]
                    left = BUDGET_SECONDS - (time.monotonic() - started)
                    try:
                        reads[dpath] = fut.result(timeout=max(1, left))
                    except Exception:  # noqa: BLE001 - timeout or a failed call
                        skipped.append(dpath)
                    finally:
                        png.unlink(missing_ok=True)

            for dpath, _ in pending:
                text = reads.get(dpath)
                if text is None:
                    continue                      # already recorded in `skipped`
                if not text:
                    notes.append(f"{dpath}: cell has writing but no character was "
                                 f"legible enough to transcribe — type it in by hand")
                    continue
                if dpath.endswith(".vin"):
                    val = _value_of(text)
                    if val is not None:
                        values[dpath.replace(".vin", ".stated_value")] = val
                    # The schedule puts stated value and VIN in ONE cell —
                    # "58,000  VIN 1FDUF4GT6NEC11223" — so the crop returns both.
                    # Writing the whole string into .vin overwrote a VIN the
                    # whole-page pass had already read correctly.
                    vin = _vin_of(text)
                    if vin:
                        values[dpath] = vin
                    else:
                        notes.append(f"{dpath}: read “{text}” from the schedule cell "
                                     f"but found no 17-character VIN in it — check by hand")
                else:
                    values[dpath] = text
    if skipped:
        notes.append(f"ran out of verification time before re-reading "
                     f"{', '.join(skipped[:8])}"
                     + (f" and {len(skipped) - 8} more" if len(skipped) > 8 else "")
                     + " — those come from a single unverified reading; check them")
    return {"values": values, "notes": notes, "scanned": scanned}
