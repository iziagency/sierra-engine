"""Extract the geometric layout of the flat CAP application PDF.

Dumps, per page:
  - table cells detected by PyMuPDF's table finder (bbox + contained text + text color)
  - small drawn squares (the checkbox glyphs)
  - all text spans with color, to distinguish field labels from option text

Outputs build/layout_raw.json plus annotated PNGs (cells red, squares green)
so placement can be visually verified before any widget is created.
"""
from __future__ import annotations

import json
import os
import sys

import fitz

SRC = r"C:\dev\sierra-pacific\app-form\source\2026 CAP app new.pdf"
OUT_DIR = r"C:\dev\sierra-pacific\app-form\build"


def span_color(span) -> str:
    c = span.get("color", 0)
    return f"#{c:06x}"


def main() -> None:
    os.makedirs(OUT_DIR, exist_ok=True)
    doc = fitz.open(SRC)
    layout = []

    for pno, page in enumerate(doc):
        entry = {"page": pno + 1, "width": page.rect.width, "height": page.rect.height}

        # --- text spans with color ---
        spans = []
        for block in page.get_text("dict")["blocks"]:
            for line in block.get("lines", []):
                for sp in line["spans"]:
                    text = sp["text"].strip()
                    if not text:
                        continue
                    spans.append({
                        "text": text,
                        "bbox": [round(v, 1) for v in sp["bbox"]],
                        "color": span_color(sp),
                        "size": round(sp["size"], 1),
                    })
        entry["spans"] = spans

        # --- small squares from drawings (checkbox glyphs) ---
        squares = []
        for d in page.get_drawings():
            r = d["rect"]
            w, h = r.width, r.height
            if 5 <= w <= 14 and 5 <= h <= 14 and abs(w - h) <= 2.5:
                squares.append([round(v, 1) for v in (r.x0, r.y0, r.x1, r.y1)])
        # dedupe near-identical rects (stroke+fill can duplicate)
        squares.sort()
        deduped = []
        for s in squares:
            if deduped and abs(s[0] - deduped[-1][0]) < 2 and abs(s[1] - deduped[-1][1]) < 2:
                continue
            deduped.append(s)
        entry["squares"] = deduped

        # --- table cells ---
        cells = []
        try:
            tf = page.find_tables()
            for t in tf.tables:
                for c in t.cells:
                    if c is None:
                        continue
                    rect = fitz.Rect(c)
                    text = page.get_text("text", clip=rect).strip()
                    cells.append({
                        "bbox": [round(v, 1) for v in c],
                        "text": text.replace("\n", " | "),
                    })
        except Exception as exc:  # table finder can fail on odd pages
            entry["table_error"] = str(exc)
        entry["cells"] = cells

        layout.append(entry)

        # --- annotated render ---
        pix_page = doc[pno]
        shape = pix_page.new_shape()
        for c in cells:
            shape.draw_rect(fitz.Rect(c["bbox"]))
        shape.finish(color=(1, 0, 0), width=0.6)
        for s in deduped:
            shape.draw_rect(fitz.Rect(s))
        shape.finish(color=(0, 0.7, 0), width=1.2)
        shape.commit()
        pix = pix_page.get_pixmap(dpi=110)
        pix.save(os.path.join(OUT_DIR, f"annot_p{pno + 1}.png"))

    with open(os.path.join(OUT_DIR, "layout_raw.json"), "w", encoding="utf-8") as f:
        json.dump(layout, f, indent=1)

    # summary to stdout
    for e in layout:
        print(
            f"page {e['page']}: cells={len(e['cells'])} squares={len(e['squares'])} "
            f"spans={len(e['spans'])}" + (" TABLE_ERROR" if "table_error" in e else "")
        )
    colors = {}
    for e in layout:
        for sp in e["spans"]:
            colors[sp["color"]] = colors.get(sp["color"], 0) + 1
    print("span colors:", json.dumps(colors))


if __name__ == "__main__":
    sys.exit(main())
