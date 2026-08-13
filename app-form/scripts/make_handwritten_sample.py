"""Generate a simulated handwritten-and-scanned CAP application (test fixture).

Writes cursive-style answers onto the FLAT source form (pages 1 and 5) using
the geometry from build/layout_raw.json, with per-entry jitter so it reads
like handwriting, then renders the pages as 'scanned' PNGs.
"""
from __future__ import annotations

import argparse
import json
import os
import random

import fitz

_APPFORM = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(_APPFORM, "source", "2026 CAP app new.pdf")
LAYOUT = os.path.join(_APPFORM, "build", "layout_raw.json")
OUT_DIR = os.path.join(_APPFORM, "build")
# A Windows handwriting font. This tool only ever runs on a developer's Windows
# box to mint a fake handwritten application for testing the extractor — it is
# not engine code and does not ship to the client. Excluded from the absolute-
# path guard by name for that reason (see tests/test_no_absolute_paths.py).
FONT = r"C:\Windows\Fonts\segoesc.ttf"

random.seed(7)

# Two fixtures, not one. A single sample cannot tell a systematic transcription
# fault from bad luck with one set of digits, and the first run of this fixture
# returned three different wrong FEINs on three attempts while getting every VIN
# right — so the second client deliberately varies what is hard: a longer legal
# name, an MC and a CA filing number, a location that differs from the mailing
# address, four vehicles instead of three, and identifiers with no repeated runs
# of digits for the reader to lean on.
CLIENTS = {
    "borderline": {
        "p1": {
            "Doing business as or DBA": "Borderline Recovery",
            "First named insured on filing": "Borderline Recovery LLC",
            "FEIN or social security number": "82-5566771",
            "Owner name": "Maria Soto",
            "Owner email": "maria@borderlinerecovery.com",
            "Contact name": "Maria Soto",
            "Contact cell phone": "760-555-0193",
            "Office phone": "760-555-0110",
            "Total number of vehicles": "3",
            "Total number of drivers": "2",
            "US DOT number": "4102987",
            "Mailing full address": "1420 W Main St, El Centro, CA 92243",
            "Location full address": "same",
            "Current auto insurance carrier": "National Casualty",
            "Current auto policy expires": "10/12/26",
            "Expiring premium": "41,000",
            "Years with auto insurance": "4",
            "How did you find Sierra": "referral from AAA rep",
        },
        "checks": ["LLC", "English"],
        "no_rows": ["Cross state lines", "Home based business", "New venture"],
        "p5_header": {
            "Total number of vehicles = power units + trailers": "3",
            "Total stated value of all vehicles": "164,000",
            "Total number of power units (= fleet)": "3",
            "Total number of trailers": "0",
        },
        "p5_rows": [
            ("2022 Ford F-450 wheel lift 16,500 tows 1", "58,000  VIN 1FDUF4GT6NEC11223"),
            ("2020 Chevy 6500 rollback 19,500 tows 1", "72,000  VIN 1HTKJPVK8LH334455"),
            ("2017 Ford F-350 wheel lift 14,000 tows 1", "34,000  VIN 1FDRF3G64HEB99887"),
        ],
    },
    "ridgeline": {
        "p1": {
            "Doing business as or DBA": "Ridgeline Towing",
            "First named insured on filing": "Ridgeline Towing & Recovery Inc",
            "FEIN or social security number": "47-2938104",
            "Owner name": "Danny Okafor",
            "Owner email": "danny@ridgelinetow.com",
            "Contact name": "Priya Raman",
            "Contact cell phone": "559-555-0472",
            "Office phone": "559-555-0281",
            "Total number of vehicles": "4",
            "Total number of drivers": "3",
            "State filing number": "0489120",
            "US DOT number": "3877215",
            "MC number": "1204663",
            "Mailing full address": "812 N Cedar Ave, Fresno, CA 93701",
            "Location full address": "4455 S Golden State Blvd, Fresno, CA 93725",
            "Website address": "ridgelinetow.com",
            "Current auto insurance carrier": "Prime Insurance Company",
            "Current auto policy expires": "03/01/27",
            "Expiring premium": "68,400",
            "Years with auto insurance": "7",
            "How did you find Sierra": "Google search",
        },
        "checks": ["Corporation", "Spanish"],
        "no_rows": ["Home based business", "New venture"],
        "p5_header": {
            "Total number of vehicles = power units + trailers": "4",
            "Total stated value of all vehicles": "343,500",
            "Total number of power units (= fleet)": "4",
            "Total number of trailers": "0",
        },
        "p5_rows": [
            ("2021 Peterbilt 337 rollback 33,000 tows 1", "145,000  VIN 2NP3LJ0X5MM123456"),
            ("2019 Ford F-550 wheel lift 19,500 tows 1", "62,500  VIN 1FDUF5HT4KEC77219"),
            ("2023 Ram 5500 rollback 19,500 tows 1", "89,000  VIN 3C7WRNBL9PG512348"),
            ("2016 Hino 258 wheel lift 26,000 tows 2", "47,000  VIN 5PVNV8JT8G4S60114"),
        ],
    },
}


def jitter_write(page, x, y, text, size=None):
    size = size or random.uniform(9.5, 11.5)
    page.insert_text(
        fitz.Point(x + random.uniform(-1, 2), y + random.uniform(-1.5, 1.5)),
        text, fontsize=size, fontfile=FONT, fontname="handw",
        color=(0.08, 0.09, 0.35),
    )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--client", default="borderline", choices=sorted(CLIENTS))
    args = ap.parse_args()
    cfg = CLIENTS[args.client]
    P1_TEXT, P1_CHECKS = cfg["p1"], cfg["checks"]
    P1_NO_ROWS, P5_HEADER, P5_ROWS = cfg["no_rows"], cfg["p5_header"], cfg["p5_rows"]

    layout = json.load(open(LAYOUT, encoding="utf-8"))
    doc = fitz.open(SRC)

    for pno, text_map, checks, no_rows in ((0, P1_TEXT, P1_CHECKS, P1_NO_ROWS),
                                           (4, {**P5_HEADER}, [], [])):
        page = doc[pno]
        info = layout[pno]
        cells = [(fitz.Rect(c["bbox"]), c["text"]) for c in info["cells"]]
        spans = info["spans"]
        squares = [fitz.Rect(s) for s in info["squares"]]

        def cell_of(label):
            for r, t in cells:
                if t.replace(" | ", " ").strip().lower() == label.lower():
                    return r
            for r, t in cells:
                if label.lower() in t.replace(" | ", " ").strip().lower():
                    return r
            return None

        def answer_cell_after(label_rect):
            row = [
                (r, t) for r, t in cells
                if abs(((r.y0 + r.y1) / 2) - ((label_rect.y0 + label_rect.y1) / 2)) < 4
                and r.x0 >= label_rect.x1 - 2
            ]
            row.sort(key=lambda rt: rt[0].x0)
            return row[0][0] if row else None

        for label, value in text_map.items():
            lc = cell_of(label)
            if lc is None:
                print(f"  p{pno+1}: label not found: {label}")
                continue
            ac = answer_cell_after(lc)
            if ac is None:
                print(f"  p{pno+1}: no answer cell for: {label}")
                continue
            jitter_write(page, ac.x0 + 6, ac.y1 - 7, value)

        # X marks on named option squares
        for opt in checks:
            hit = [s for s in spans if s["text"].strip().rstrip(":") == opt]
            if hit:
                sr = min(
                    squares,
                    key=lambda q: abs(q.x1 - fitz.Rect(hit[0]["bbox"]).x0) + abs(q.y0 - hit[0]["bbox"][1]),
                )
                jitter_write(page, sr.x0 + 0.5, sr.y1 - 0.5, "X", size=9)
        for row_label in no_rows:
            lc = cell_of(row_label)
            if lc is None:
                continue
            row_squares = [q for q in squares
                           if abs(((q.y0 + q.y1) / 2) - ((lc.y0 + lc.y1) / 2)) < 6 and q.x0 > lc.x1 - 2]
            if row_squares:
                sq = sorted(row_squares, key=lambda q: q.x0)[0]  # first square = No
                jitter_write(page, sq.x0 + 0.5, sq.y1 - 0.5, "X", size=9)

    # page 5 vehicle rows: number cells "1."/"2."/"3." and their "$" cells
    page = doc[4]
    info = layout[4]
    for i, (desc, value) in enumerate(P5_ROWS, start=1):
        num_cells = [c for c in info["cells"] if c["text"].strip() == f"{i}."]
        dol_cells = [c for c in info["cells"] if c["text"].strip() == "$"]
        if num_cells:
            r = fitz.Rect(num_cells[0]["bbox"])
            jitter_write(page, r.x0 + 18, r.y1 - 7, desc)
            dol_same_row = [c for c in dol_cells
                            if abs(fitz.Rect(c["bbox"]).y0 - r.y0) < 4]
            if dol_same_row:
                dr = fitz.Rect(dol_same_row[0]["bbox"])
                jitter_write(page, dr.x0 + 14, dr.y1 - 7, value)

    for pno in (0, 4):
        pix = doc[pno].get_pixmap(dpi=140)
        pix.save(f"{OUT_DIR}\\{args.client}_scan_p{pno + 1}.png")
    print(f"scans saved: {args.client}_scan_p1.png, {args.client}_scan_p5.png")


if __name__ == "__main__":
    main()
