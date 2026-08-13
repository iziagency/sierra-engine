"""QA pass: fill every field with a visible marker and render each page.

Text fields get their own field name as value (so mapping errors are visible),
checkboxes get checked. Renders build/qa_pN.png for visual inspection.
"""
from __future__ import annotations

import os
import sys

import fitz

_APPFORM = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DIST = os.path.join(_APPFORM, "dist")
BUILD = os.path.join(_APPFORM, "build")


def main() -> None:
    doc = fitz.open(os.path.join(DIST, "CAP_app_2026_fillable.pdf"))
    n_text = n_box = 0
    for page in doc:
        for w in page.widgets():
            if w.field_type == fitz.PDF_WIDGET_TYPE_TEXT:
                w.field_value = w.field_name
                n_text += 1
            elif w.field_type == fitz.PDF_WIDGET_TYPE_CHECKBOX:
                w.field_value = True
                n_box += 1
            w.update()
    qa_pdf = os.path.join(BUILD, "qa_filled.pdf")
    doc.save(qa_pdf)
    doc = fitz.open(qa_pdf)
    for i, page in enumerate(doc):
        pix = page.get_pixmap(dpi=110)
        pix.save(os.path.join(BUILD, f"qa_p{i + 1}.png"))
    print(f"filled {n_text} text widgets, {n_box} checkboxes -> {qa_pdf}")


if __name__ == "__main__":
    sys.exit(main())
