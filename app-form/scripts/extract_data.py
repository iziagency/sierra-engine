"""Dump the field values of a filled CAP application PDF back to JSON.

Used for renewals (read last year's filled app, pre-fill this year's) and for
round-trip checks. Output shape: {"fields": {field_name: value}} - feedable
straight back into fill_app.py via the raw-fields escape hatch.

Usage:
    python extract_data.py path/to/filled.pdf [out.json]
"""
from __future__ import annotations

import json
import sys

import fitz


def extract(pdf_path: str) -> dict:
    doc = fitz.open(pdf_path)
    fields: dict[str, object] = {}
    for page in doc:
        for w in page.widgets():
            if w.field_type == fitz.PDF_WIDGET_TYPE_CHECKBOX:
                if w.field_value not in (None, False, "", "Off"):
                    fields[w.field_name] = True
            else:
                v = (w.field_value or "").strip()
                if v:
                    fields[w.field_name] = v
    return {"fields": fields}


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__, file=sys.stderr)
        return 2
    data = extract(sys.argv[1])
    out = sys.argv[2] if len(sys.argv) > 2 else None
    text = json.dumps(data, indent=1)
    if out:
        open(out, "w", encoding="utf-8").write(text)
        print(f"{len(data['fields'])} fields -> {out}")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
