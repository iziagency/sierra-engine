"""Generate the fillable AcroForm template from the flat 2026 CAP application.

Reads the source PDF, reconstructs each page's table grid, classifies text by
color (dark blue = field label, pale blue = option/placeholder text), and adds:
  - text widgets over empty answer cells
  - checkbox widgets over the drawn squares
  - companion text widgets after options that expect a value ("Yes %:", "$:")
  - one multiline widget over free-text description blocks

Fields that repeat across pages (SP policy code, fleet totals) share a single
field name so filling them once propagates everywhere.

Outputs:
  dist/CAP_app_2026_fillable.pdf
  dist/field_map.json   (name, page, type, label, group - the fill contract)
"""
from __future__ import annotations

import json
import os
import re
import sys
from collections import defaultdict

import fitz

# The 14-page `ren` file is the internal working set: the 9-page application a
# client fills out, plus the five Sierra forms that follow it in every quoting
# packet — Loss run Request form, Loss run Scores, Certificate schedule, Meta
# data and Quoting packet Checklist. Building from the 9-page `new` file gave a
# template that could only ever fill the front third of a QP.
_APPFORM = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(_APPFORM, "source", "2026_CAP_app_ren.pdf")
DIST = os.path.join(_APPFORM, "dist")

LABEL_COLORS = {0x005493, 0x005993}
OPTION_COLORS = {0x76D6FF, 0x56C1FF}

# labels whose fields are shared across pages (same AcroForm name = same value)
SHARED_LABELS = {
    "sp policy code": "sp_policy_code",
    "total number of vehicles": "total_vehicles",
    "total number of vehicles = power units + trailers": "total_vehicles",
    "total number of drivers": "total_drivers",
    "total stated value of all vehicles": None,  # p2 is a coverage row; p5/p6 share below
    "total number of power units": "veh_total_power",
    "total number of power units (= fleet)": "veh_total_power",
    "total number of trailers": "veh_total_trailers",
}
# "total stated value of all vehicles" shares only on schedule pages
SHARED_SCHEDULE_VALUE = "veh_total_value"

MULTILINE_PREFIXES = (
    "describe",
    "overall description",
    "location details",
    "other coverages requested",
)

# pale-styled texts that are actually field labels (not options)
PALE_LABELS = {
    "sp policy code": "sp_policy_code",   # shared across all pages
    "source code": None,
    "insured name + signature + date": None,
}

# option text (normalized: lowercase, no trailing ':') -> checkbox suffix,
# companion text-field suffix (None = no companion)
OPTION_MAP = {
    "no": ("no", None),
    "yes": ("yes", None),
    "yes %": ("yes", "pct"),
    "yes #": ("yes", "num"),
    "yes the brand name is": ("yes", "brand"),
    "yes, breed of dog": ("yes", "breed"),
    "yes, provider": ("yes", "provider"),
    "no coverage requested": ("none", None),
    "no commercial locations": ("none", None),
    "no policy in force": ("no_policy", None),
    "no dash cameras installed": ("no", None),
    "no telematics installed": ("no", None),
    "$1,000,000": ("1m", None),
    "$1,000,000 each occurrence": ("1m", None),
    "$750,000": ("750k", None),
    "$500,000": ("500k", None),
    "$150,000": ("150k", None),
    "$100,000": ("100k", None),
    "$50,000": ("50k", None),
    "$25,000": ("25k", None),
    "$": ("other", "other_amt"),
    "expires": ("expires_chk", "expires"),
    "english": ("english", None),
    "spanish": ("spanish", None),
    "sole proprietor": ("sole", None),
    "corporation": ("corp", None),
    "llc": ("llc", None),
    "<12": ("lt12", None),
    "12+ hours a day": ("h12plus", None),
    "n/a": ("na", None),
    "lockbox only": ("lockbox", None),
    "other, describe": ("other", "other_desc"),
    "other, describe;": ("other", "other_desc"),
    "other": ("other", "other_desc"),
    "storage lot": ("storage_lot", None),
    "proper id provided to reclaim": ("proper_id", None),
    "notify police of illegal weapons or drugs": ("notify_police", None),
    "proper registration verified": ("registration_verified", None),
    "sierra": ("sierra", None),
    "other broker": ("other_broker", None),
}

ROWNUM_RE = re.compile(r"^(\d{1,2})\.$")
YEARBLOCK_RE = re.compile(r"^(\d{4}) insurance carrier$")


def slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")
    return re.sub(r"_+", "_", s)


def norm_opt(text: str) -> str:
    # the source doc has a "$;" typo where "$:" was intended - strip both
    return text.rstrip(":;").strip().lower()


class PageModel:
    def __init__(self, page: fitz.Page):
        self.page = page
        self.spans = []
        for block in page.get_text("dict")["blocks"]:
            for line in block.get("lines", []):
                for sp in line["spans"]:
                    t = sp["text"].strip()
                    if t:
                        self.spans.append(
                            {"text": t, "rect": fitz.Rect(sp["bbox"]), "color": sp["color"]}
                        )
        self.squares = self._squares()
        self.cells = self._cells()

    def _squares(self):
        found = []
        for d in self.page.get_drawings():
            r = d["rect"]
            if 5 <= r.width <= 14 and 5 <= r.height <= 14 and abs(r.width - r.height) <= 2.5:
                found.append(r)
        found.sort(key=lambda r: (round(r.y0), round(r.x0)))
        dedup = []
        for r in found:
            if dedup and abs(r.x0 - dedup[-1].x0) < 2 and abs(r.y0 - dedup[-1].y0) < 2:
                continue
            dedup.append(r)
        return dedup

    def _cells(self):
        cells = []
        seen = set()
        for t in self.page.find_tables().tables:
            for c in t.cells:
                if c is None:
                    continue
                key = tuple(round(v) for v in c)
                if key in seen:
                    continue
                seen.add(key)
                cells.append(fitz.Rect(c))
        return cells

    def spans_in(self, rect: fitz.Rect):
        out = []
        for sp in self.spans:
            mid = fitz.Point((sp["rect"].x0 + sp["rect"].x1) / 2,
                             (sp["rect"].y0 + sp["rect"].y1) / 2)
            if rect.contains(mid):
                out.append(sp)
        out.sort(key=lambda s: (round(s["rect"].y0), s["rect"].x0))
        return out

    def squares_in(self, rect: fitz.Rect):
        return sorted(
            [r for r in self.squares if rect.contains(fitz.Point((r.x0 + r.x1) / 2, (r.y0 + r.y1) / 2))],
            key=lambda r: r.x0,
        )

    def rows(self):
        """Group cells into rows by y-center."""
        buckets = defaultdict(list)
        for c in self.cells:
            buckets[round((c.y0 + c.y1) / 2 / 4)].append(c)
        merged = {}
        for k in sorted(buckets):
            placed = False
            for mk in list(merged):
                if abs(mk - k) <= 1:
                    merged[mk].extend(buckets[k])
                    placed = True
                    break
            if not placed:
                merged[k] = list(buckets[k])
        return [sorted(v, key=lambda c: c.x0) for _, v in sorted(merged.items())]


class TemplateBuilder:
    def __init__(self):
        self.doc = fitz.open(SRC)
        self.fields = []          # field_map entries
        self.used_names = set()

    # ---------- widget helpers ----------
    def _unique(self, name: str, shared: bool = False) -> str:
        if shared:
            return name
        base, n = name, 2
        while name in self.used_names:
            name = f"{base}_{n}"
            n += 1
        return name

    def add_text(self, page, rect, name, label, group=None, multiline=False, shared=False):
        name = self._unique(name, shared)
        if name in self.used_names and shared:
            pass  # same-name field on another page: still add widget
        w = fitz.Widget()
        w.rect = rect
        w.field_name = name
        w.field_type = fitz.PDF_WIDGET_TYPE_TEXT
        w.text_fontsize = 0 if not multiline else 8
        w.text_font = "helv"
        if multiline:
            w.field_flags = fitz.PDF_TX_FIELD_IS_MULTILINE
        page.add_widget(w)
        if name not in self.used_names:
            self.fields.append({
                "name": name, "page": page.number + 1,
                "type": "multiline" if multiline else "text",
                "label": label, "group": group or name,
            })
            self.used_names.add(name)

    def add_checkbox(self, page, rect, name, label, group):
        name = self._unique(name)
        w = fitz.Widget()
        w.rect = fitz.Rect(rect.x0 - 0.5, rect.y0 - 0.5, rect.x1 + 0.5, rect.y1 + 0.5)
        w.field_name = name
        w.field_type = fitz.PDF_WIDGET_TYPE_CHECKBOX
        page.add_widget(w)
        self.fields.append({
            "name": name, "page": page.number + 1, "type": "checkbox",
            "label": label, "group": group,
        })
        self.used_names.add(name)

    # ---------- cell processors ----------
    def process_option_cell(self, page, model, cell, base, prefix, label):
        """Cell containing checkbox squares and pale option text."""
        squares = model.squares_in(cell)
        opts = [s for s in model.spans_in(cell) if s["color"] in OPTION_COLORS]
        for i, sq in enumerate(squares):
            nxt_x = squares[i + 1].x0 if i + 1 < len(squares) else cell.x1
            texts = [o for o in opts if sq.x1 - 1 <= o["rect"].x0 < nxt_x - 1]
            opt_text = " ".join(t["text"] for t in texts).strip()
            key = norm_opt(opt_text)
            suffix, companion = OPTION_MAP.get(key, (slug(key) or f"opt{i+1}", None))
            self.add_checkbox(page, sq, f"{prefix}{base}_{suffix}", f"{label} - {opt_text}", f"{prefix}{base}")
            if companion and texts:
                tx0 = max(t["rect"].x1 for t in texts) + 3
                tx1 = nxt_x - 6 if i + 1 < len(squares) else cell.x1 - 3
                if tx1 - tx0 > 12:
                    r = fitz.Rect(tx0, cell.y0 + 2, tx1, cell.y1 - 2)
                    self.add_text(page, r, f"{prefix}{base}_{companion}",
                                  f"{label} - {opt_text} value", f"{prefix}{base}")

    def process_anchor_cell(self, page, model, cell, base, prefix, label):
        """Cell whose only content is a pale '$' anchor -> text field after it."""
        opts = [s for s in model.spans_in(cell) if s["color"] in OPTION_COLORS]
        anchor = opts[0]
        r = fitz.Rect(anchor["rect"].x1 + 3, cell.y0 + 2, cell.x1 - 3, cell.y1 - 2)
        self.add_text(page, r, f"{prefix}{base}", label, f"{prefix}{base}")

    def add_blank_grid(self, page, label_prefix, name_prefix):
        """Fields for a ruled page that carries no printed labels.

        The Underwriting Qs sheet is a 28-row, 2-column grid the underwriter
        writes into by hand — question on the left, answer on the right. The
        label-driven pass finds nothing there because there is nothing printed
        to read, so the page would ship unfillable while the QP checklist still
        demands "App Qs answers all in". Emit a pair of fields per row instead.
        """
        tables = page.find_tables().tables
        if not tables:
            return 0
        tb = max(tables, key=lambda t: t.row_count)
        made = 0
        for i, row in enumerate(tb.rows, 1):
            cells = [c for c in row.cells if c]
            if len(cells) < 2:
                continue
            for col, cell in enumerate(cells[:2]):
                r = fitz.Rect(*cell)
                if r.width < 40 or r.height < 9:
                    continue
                side = "q" if col == 0 else "a"
                self.add_text(page, fitz.Rect(r.x0 + 2, r.y0 + 1, r.x1 - 2, r.y1 - 1),
                              f"{name_prefix}_{side}{i}",
                              f"{label_prefix} {i} " + ("question" if col == 0 else "answer"),
                              group=f"{name_prefix}_{i}", multiline=True)
                made += 1
        return made

    # ---------- main ----------
    def build(self):
        for page in self.doc:
            model = PageModel(page)
            pno = page.number + 1
            head = " ".join(page.get_text().split()[:6])
            if "Underwriting Qs" in head:
                n = self.add_blank_grid(page, "Underwriting Q", "uw")
                print(f"page {pno}: blank grid, {n} fields")
                continue
            prefix = f"p{pno}_"
            pending_ml_label = None
            ml_rects = []
            p7_row_idx = 0
            p9_block = None
            p9_seen_years = set()

            def flush_ml():
                nonlocal pending_ml_label, ml_rects
                if pending_ml_label and ml_rects:
                    union = ml_rects[0]
                    for r in ml_rects[1:]:
                        union |= r
                    r = fitz.Rect(union.x0 + 2, union.y0 + 2, union.x1 - 2, union.y1 - 2)
                    self.add_text(page, r, f"{prefix}{slug(pending_ml_label)}",
                                  pending_ml_label, multiline=True)
                pending_ml_label, ml_rects = None, []

            for row in model.rows():
                row_texts = []
                for cell in row:
                    row_texts.append([s for s in model.spans_in(cell)])
                is_empty_row = all(
                    not [s for s in texts if s["color"] in LABEL_COLORS | OPTION_COLORS]
                    and not model.squares_in(cell)
                    for cell, texts in zip(row, row_texts)
                )

                # ---- multiline collection ----
                if pending_ml_label:
                    if is_empty_row:
                        for cell in row:
                            ml_rects.append(cell)
                        continue
                    flush_ml()

                # ---- page 7 driver rows ----
                if pno == 7 and is_empty_row and len(row) == 2:
                    p7_row_idx += 1
                    n = f"{p7_row_idx:02d}"
                    self.add_text(page, fitz.Rect(row[0].x0 + 2, row[0].y0 + 2, row[0].x1 - 2, row[0].y1 - 2),
                                  f"{prefix}drv{n}_name", f"Driver {p7_row_idx} name", f"{prefix}drv{n}")
                    self.add_text(page, fitz.Rect(row[1].x0 + 2, row[1].y0 + 2, row[1].x1 - 2, row[1].y1 - 2),
                                  f"{prefix}drv{n}_details", f"Driver {p7_row_idx} details", f"{prefix}drv{n}")
                    continue

                if is_empty_row:
                    continue

                current_base = None
                current_label = None
                for cell, texts in zip(row, row_texts):
                    labels = [s for s in texts if s["color"] in LABEL_COLORS]
                    options = [s for s in texts if s["color"] in OPTION_COLORS]
                    squares = model.squares_in(cell)
                    label_text = " ".join(s["text"] for s in labels).strip()

                    # --- label cell ---
                    if label_text:
                        norm = label_text.rstrip(":").strip().lower()
                        low = label_text.lower()
                        if any(low.startswith(p) for p in MULTILINE_PREFIXES):
                            pending_ml_label = label_text.rstrip(":")
                            continue
                        # page 9 year blocks
                        m = YEARBLOCK_RE.match(norm)
                        if pno == 9 and m:
                            year = m.group(1)
                            key = year if year not in p9_seen_years else f"{year}b"
                            p9_seen_years.add(year)
                            p9_block = f"lr{key}"
                            current_base, current_label = f"{p9_block}_carrier", label_text
                            continue
                        if pno == 9 and p9_block and norm in (
                            "policy number", "effective dates", "annual premium",
                            "broker on file with carrier",
                        ):
                            sub = {"policy number": "policy_number",
                                   "effective dates": "effective_dates",
                                   "annual premium": "premium",
                                   "broker on file with carrier": "broker"}[norm]
                            current_base, current_label = f"{p9_block}_{sub}", label_text
                            continue
                        current_base, current_label = slug(label_text), label_text
                        # shared names
                        if norm in SHARED_LABELS and SHARED_LABELS[norm]:
                            current_base = ("__shared__", SHARED_LABELS[norm], label_text)
                        elif norm == "total stated value of all vehicles" and pno in (5, 6):
                            current_base = ("__shared__", SHARED_SCHEDULE_VALUE, label_text)
                        continue

                    # --- numbered row cell (schedules p5/p6, contracts p2) ---
                    if options and not squares:
                        joined = " ".join(o["text"] for o in options).strip()
                        njoined = joined.rstrip(":;").strip().lower()
                        # pale-styled labels that are real field labels
                        if njoined in PALE_LABELS:
                            shared_name = PALE_LABELS[njoined]
                            current_label = joined.rstrip(":;")
                            if shared_name:
                                current_base = ("__shared__", shared_name, current_label)
                            else:
                                current_base = slug(joined)
                            continue
                        # contracts row: "N.        %" comes as a single span
                        cm = re.match(r"^([1-4])\.\s+%$", options[0]["text"].strip())
                        if pno == 2 and cm:
                            n = int(cm.group(1))
                            bb = options[0]["rect"]
                            self.add_text(page, fitz.Rect(bb.x0 + 10, cell.y0 + 2, bb.x1 - 34, cell.y1 - 2),
                                          f"{prefix}contract{n}_name", f"Contract {n} name", f"{prefix}contract{n}")
                            self.add_text(page, fitz.Rect(bb.x1 - 32, cell.y0 + 2, bb.x1 - 8, cell.y1 - 2),
                                          f"{prefix}contract{n}_pct", f"Contract {n} %", f"{prefix}contract{n}")
                            continue
                        m = ROWNUM_RE.match(options[0]["text"].strip())
                        if pno in (5, 6) and m:
                            n = int(m.group(1))
                            anchor = options[0]["rect"]
                            r = fitz.Rect(anchor.x1 + 3, cell.y0 + 2, cell.x1 - 3, cell.y1 - 2)
                            fname = f"{prefix}veh{n:02d}_desc" if pno == 5 else f"{prefix}veh{n:02d}_yearmaker"
                            self.add_text(page, r, fname, f"Vehicle {n} description", f"{prefix}veh{n:02d}")
                            current_base = f"veh{n:02d}"
                            current_label = f"Vehicle {n}"
                            continue
                        if joined == "$" and current_base:
                            anchor = options[0]["rect"]
                            r = fitz.Rect(anchor.x1 + 3, cell.y0 + 2, cell.x1 - 3, cell.y1 - 2)
                            if isinstance(current_base, tuple):
                                self.add_text(page, r, current_base[1], current_base[2], shared=True)
                            elif pno == 5 and current_base.startswith("veh"):
                                self.add_text(page, r, f"{prefix}{current_base}_value_vin",
                                              f"{current_label} stated value + VIN", f"{prefix}{current_base}")
                            else:
                                self.add_text(page, r, f"{prefix}{current_base}", current_label,
                                              f"{prefix}{current_base}")
                            current_base = None
                            continue
                        # pale informational text (totals, section subheads) -> ignore
                        continue

                    # --- checkbox cell ---
                    if squares:
                        if isinstance(current_base, tuple):
                            current_base = slug(current_base[2])
                        if pno == 6 and current_base and current_base.startswith("veh"):
                            base = f"{current_base}_onhook"
                        elif current_base:
                            base = current_base
                        elif pno == 9 and p9_block:
                            base = p9_block
                        elif pno == 4:
                            # unlabeled No/Yes pair, question 4.10 in JC's guide
                            base = "q4_10"
                        else:
                            base = f"row{round(cell.y0)}"
                        self.process_option_cell(page, model, cell, base, prefix,
                                                 current_label or base)
                        current_base = None
                        continue

                    # --- empty answer cell ---
                    if current_base:
                        r = fitz.Rect(cell.x0 + 2, cell.y0 + 2, cell.x1 - 3, cell.y1 - 2)
                        if isinstance(current_base, tuple):
                            self.add_text(page, r, current_base[1], current_base[2], shared=True)
                        else:
                            self.add_text(page, r, f"{prefix}{current_base}", current_label,
                                          f"{prefix}{current_base}")
                        current_base = None

            flush_ml()

        os.makedirs(DIST, exist_ok=True)
        out_pdf = os.path.join(DIST, "CAP_app_2026_fillable.pdf")
        self.doc.save(out_pdf)
        with open(os.path.join(DIST, "field_map.json"), "w", encoding="utf-8") as f:
            json.dump(self.fields, f, indent=1)

        counts = defaultdict(lambda: defaultdict(int))
        for fld in self.fields:
            counts[fld["page"]][fld["type"]] += 1
        for pg in sorted(counts):
            print(f"page {pg}: " + ", ".join(f"{k}={v}" for k, v in sorted(counts[pg].items())))
        print(f"TOTAL fields: {len(self.fields)}")
        print(f"saved: {out_pdf}")


if __name__ == "__main__":
    sys.exit(TemplateBuilder().build())
