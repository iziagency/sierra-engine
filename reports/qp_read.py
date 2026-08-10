r"""Read a Sierra Pacific CAP application QP PDF into a dossier-shaped dict.

JC, on the recorded 7.22 call: "the first thing we want to do is we want to
be able to take that QP and populate the RTS... maybe you just upload the QP
directly into the Slack channel, use this as the basis for all the
information to fill out the RTS app." rts_fill.py could only read an
already-built dossier (clients/<slug>/state.json); it had no PDF capability
at all. This module is the missing link - QP PDF -> dossier dict, in the same
shape state.json already uses, so it slots into fill_app.py's existing merge
semantics (see merge_into_dossier below) instead of inventing a parallel path.

The embedded Sierra Pacific CAP app pages KEEP their AcroForm fields, so this
reads page.widgets() (field_name / field_value) - never OCR, never regex over
prose. Only the widget-bearing "app" pages are read (verified on the real
Lakeside QP: pages 1-9 are the CAP application, page 17 is a "Meta data" page,
page 18 is the internal quoting-packet checklist). Pages 10-15 (carrier
loss-run PDFs folded into the packet) and page 18 (the checklist) carry no
client data and are skipped entirely; page 16 ("more Loss runs") repeats a
subset of what page 9 already gives more completely and is also skipped.

Everything below was checked field-by-field against the real document:
    LAKES CAP tow QP 7.27.26.pdf - 36 pages, 729 AcroForm widgets, 213 with
    values, downloaded from the client's Drive on 2026-07-27.
A few of the QP's own field NAMES are garbled (a dropped letter here and
there - "descript on", "Dr ver name", "nsurance carr er" - almost certainly a
font-subsetting artifact from whatever tool authored the template, not
something introduced by reading it). Names are copied verbatim from the real
PDF rather than "corrected", since the correction would just be a guess and
the exact strings are what actually has to be matched.

Design notes worth knowing before changing this file:

* "Same" placeholders. Several fields print the literal word "Same" meaning
  "same as the field above" (e.g. "SP Contact name" = "Same" meaning "same as
  the owner name"). SAME_AS resolves these against the specific field each one
  echoes - verified against the real form's layout and cross-checked against
  the existing Lakeside dossier, not guessed from the word alone.

* Loss runs are POSITIONAL, not name-based. Page 9 has five repeating blocks
  of 7 widgets; the "insurance carrier" field's own name carries a year label,
  but on the real QP that label is simply wrong for two of the five blocks
  (both say "2021" while one of them is actually the 2022 policy, identified
  by its effective dates). fill_app.py's own loss_runs()/lossrun_page_values()
  document this exact quirk for the OUTPUT template ("the real QP has '2025'
  written into the row printed '2024'"). The fix here is the same one: derive
  each block's year from its effective-dates text, never from the row label.

* Vehicles/drivers are combined-string fields, not one-field-per-datum. "Veh
  info 1" holds "2016 Hino     CC     25,500     2" (year, maker, body type,
  GVW, max towed) as ONE text value - this is the QP's own field, not scraped
  page text, so parsing it is not "OCR over prose", but it IS a best-effort
  split (runs of 2+ spaces as the field's own internal delimiter). A maker
  that is itself multiple words is still captured whole (only the leading
  4-digit year is split off); nothing past what four/five delimited tokens
  supply is invented.

* Deliberately NOT extracted (never guessed):
    - auto_liability's 750k/500k/other tiers: the QP has 3 more checkboxes
      here (undefined_12/13/14) with no name to key off - only "no coverage"
      and "1M" are read.
    - Vehicles/location checkbox groups the QP itself names "undefined_NNN"
      (e.g. page 6's per-vehicle on-hook tier buttons, page 8's dogs/keys/
      weapons/relinquish-vehicle policies). Page 6's on-hook AMOUNT field is
      still read (it is a plain dollar value, not a checkbox), so a vehicle
      with a custom on-hook figure is still recovered; a vehicle on a
      standard tier with nothing typed in the amount box is not.
    - Page 16 and 18 (see module docstring above).
    - "SP UM" is read as `coverages.umbrella`, not "uninsured motorist": our
      own fillable template (app-form/dist/field_map.json) has exactly one
      field of this kind - p2_umbrella_coverage_limit_none/other/other_amt -
      no separate UM/UIM limit field exists anywhere in it, and "SP UM" sits
      last in the QP's coverage list, right before workers comp, which is
      where umbrella normally sits on a CAP quote (uninsured motorist would
      normally sit next to auto liability, not after inland marine). Flagged
      here in case a future QP makes the alternate reading obvious.

Usage (standalone, for a quick look at any QP):
    python qp_read.py path/to/some_QP.pdf
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import fitz

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "app-form" / "scripts"))
sys.path.insert(0, str(ROOT / "shared"))
from fill_app import deep_merge  # noqa: E402,F401 - needs sys.path set up first
# Reused so "is this the same value?" and "how do we write this value?" can never
# drift apart into two different notions of a date or a phone number.
from formatting import format_date, format_phone  # noqa: E402


class QPReadError(Exception):
    """The PDF could not be read as a Sierra Pacific QP at all (missing,
    unreadable, or carrying none of the expected AcroForm structure)."""


# ---------------------------------------------------------------- low-level helpers

NA_WORDS = {"n/a", "na", "none", "-", ""}


def _text(v) -> str:
    return "" if v in (None, "Off") else str(v).strip()


def _is_on(v) -> bool:
    return str(v).strip().lower() == "on"


def _clean(v):
    """A blank AcroForm value, or a placeholder like "N/A", both mean nothing
    was actually said here - never store the placeholder word itself."""
    t = _text(v)
    return None if t.lower() in NA_WORDS else t


def _to_number(text):
    """Parse "50,000" / "$1,234.50" -> 50000 / 1234.5; int when whole, since
    that is how the dossier already stores these (see client_data.example.json
    - "total_stated_value": 285000, not 285000.0). None, never a guess, when
    the text is not actually a number."""
    if text in (None, ""):
        return None
    try:
        n = float(str(text).replace("$", "").replace(",", "").strip())
    except ValueError:
        return None
    return int(n) if n == int(n) else n


def _amount_to_k(text: str) -> str:
    """"150,000" -> "150k", matching the dossier's own shorthand for coverage
    tiers with fixed dollar buckets (100k/50k/25k/150k, see on_hook in
    app-form/config/client_data.example.json and the real Lakeside dossier).
    Falls back to the raw text when it is not a clean multiple of 1000,
    rather than rounding or guessing a bucket."""
    n = _to_number(text)
    if isinstance(n, int) and n > 0 and n % 1000 == 0:
        return f"{n // 1000}k"
    return text.strip()


def _year_from_effective_dates(text: str):
    """"8.3.22 - 8.3.23" -> 2022. See the loss-runs design note above for why
    this - and not the row's field-name label - is the source of truth for
    which policy year a block actually holds."""
    m = re.match(r"\s*(\d{1,2})[.\-/](\d{1,2})[.\-/](\d{2,4})", text)
    if not m:
        return None
    yr = m.group(3)
    return 2000 + int(yr) if len(yr) == 2 else int(yr)


def _load_widgets(pdf_path) -> dict[int, list]:
    """1-based page number -> widgets on that page, in on-page (reading)
    order. Only pages that actually carry AcroForm widgets are included."""
    try:
        doc = fitz.open(pdf_path)
    except Exception as exc:                                    # noqa: BLE001 - any open failure is "not readable"
        raise QPReadError(f"could not open '{pdf_path}' as a PDF: {exc}") from exc
    try:
        pages: dict[int, list] = {}
        for pno in range(doc.page_count):
            widgets = list(doc[pno].widgets() or [])
            if widgets:
                pages[pno + 1] = widgets
        return pages
    finally:
        doc.close()


def _flat_fields(pages: dict[int, list]) -> dict[str, str]:
    """field_name -> field_value across ALL pages, checkboxes as "On"/absent.

    Several field names are intentionally reused across pages - "SP code" is
    a running header repeated on every app page, and "SP GKLL no coverage" /
    "SP building no coverage" / "SP BPP no coverage" appear on both the page-2
    coverage grid and the page-8 per-location schedule. That is standard
    AcroForm practice (one logical field, several on-page appearances), not a
    naming clash, so merging into one flat dict is correct here. The
    per-index repeating blocks (vehicles, drivers, loss-run years) already
    carry unique per-row suffixes and are handled separately, positionally,
    reading straight from their own page's widget list instead of this dict.
    """
    out: dict[str, str] = {}
    for widgets in pages.values():
        for w in widgets:
            if w.field_type == fitz.PDF_WIDGET_TYPE_CHECKBOX:
                if _is_on(w.field_value):
                    out[w.field_name] = "On"
            else:
                v = _text(w.field_value)
                if v:
                    out[w.field_name] = v
    return out


def _bool_pair(fields: dict, false_box: str, true_box: str):
    """A No/Yes checkbox pair -> True/False/None. None (neither checked)
    means genuinely unanswered - not the same as a "No", so it is left
    absent rather than defaulted."""
    if _is_on(fields.get(true_box)):
        return True
    if _is_on(fields.get(false_box)):
        return False
    return None


def _one_of(fields: dict, box_map: dict[str, str]):
    for box, tag in box_map.items():
        if _is_on(fields.get(box)):
            return tag
    return None


def _pct_group(fields: dict, label: str, suffix: str = "percentage"):
    """The QP's uniform "SP {label} percentage/No/Yes" triple (or "number"
    instead of "percentage" for the couple of fields the QP itself labels
    that way) -> the dossier's own convention: a bare number when a
    percentage/count was given, True/False otherwise, absent when neither box
    is checked (an unanswered question, not a "no" - see client_data.example
    .json's own comment: booleans check boxes, a number implies yes+pct)."""
    yes = _is_on(fields.get(f"SP {label} Yes"))
    no = _is_on(fields.get(f"SP {label} No"))
    if not yes and not no:
        return None
    if not yes:
        return False
    raw = _clean(fields.get(f"SP {label} {suffix}"))
    if raw is None:
        return True
    n = _to_number(raw)
    return n if n is not None else raw


def _radius_group(fields: dict, label: str, yesno_label: str | None = None):
    """Same shape as _pct_group, but radius rows sometimes carry a note past
    the number (the real Lakeside QP has "20   200mi max" for the 51-300 mile
    bucket) - rts_fill.py's own _radius() already expects that as
    "PCT (note)", so it is normalized into that shape here instead of being
    dropped or left as raw multi-space text. `yesno_label` overrides the
    label used for the Yes/No checkboxes when it differs from the
    percentage field's own label (see RADIUS_YESNO_OVERRIDE)."""
    yn = label if yesno_label is None else yesno_label
    yes = _is_on(fields.get(f"SP {yn} Yes"))
    no = _is_on(fields.get(f"SP {yn} No"))
    if not yes and not no:
        return None
    if not yes:
        return False
    raw = _clean(fields.get(f"SP {label} percentage"))
    if raw is None:
        return True
    m = re.match(r"^(\d+)\s*$", raw)
    if m:
        return int(m.group(1))
    m = re.match(r"^(\d+)\s+(\S.*)$", raw)
    if m:
        return f"{m.group(1)} ({m.group(2)})"
    return raw


# ---------------------------------------------------------------- section 1: identity (page 1)

IDENTITY_TEXT = {
    "SP DBA": "dba",
    "SP insured name": "first_named_insured",
    "SP FEIN": "fein",
    "SP owner name": "owner_name",
    "SP email": "owner_email",
    "SP Contact name": "contact_name",
    "SP contact email": "contact_email",
    "SP Contact ce phone": "contact_cell",
    "SP contact title": "contact_title",
    "Office phone": "office_phone",
    "SP state filing number": "state_filing_number",
    "SP DOT number": "usdot_number",
    "SP MC number": "mc_number",
    "SP mailing address": "mailing_address",
    "SP location address": "location_address",
    "SP website": "website",
    "SP IG": "instagram",
    "SP FB": "facebook",
    "SP Current carrier": "current_auto_carrier",
    "SP current policy expiration": "current_auto_expires",
    "SP current policy expiring premium": "expiring_premium",
    "SP years with auto insurance": "years_with_auto_insurance",
    "SP current WC carrier": "current_wc_carrier",
    "SP current WC policy expiration": "current_wc_expires",
    "SP telematics brand": "telematics_brand",
    "How did you find SP": "how_found_sierra",
}

# "Same" means "same as the field it echoes" - verified against the real
# form's page-1 layout (Owner name/email sit right above Contact name/email;
# Contact cell sits right above Office phone; Mailing address sits right
# above Location address), not guessed from the word alone.
SAME_AS = {
    "contact_name": "owner_name",
    "contact_email": "owner_email",
    "office_phone": "contact_cell",
    "location_address": "mailing_address",
}

ENTITY_TYPE_BOXES = {"SP sole": "sole_proprietor", "SP Corporation": "corporation", "SP LLC": "llc"}
LANGUAGE_BOXES = {"SP language english": "english", "SP language spanish": "spanish"}

IDENTITY_BOOL = (
    ("SP do not cross state lines", "SP cross state lines", "cross_state_lines"),
    ("SP not homebased", "SP homebased", "home_based"),
    ("SP No dash cam", "SP With dash cam", "dash_cameras"),
    ("SP No telematics", "SP with telematics", "telematics"),
    ("SP not NV", "SP NV", "new_venture"),
)


def _identity(fields: dict, warnings: list[str]) -> dict:
    company: dict = {}
    for qp_name, key in IDENTITY_TEXT.items():
        v = _clean(fields.get(qp_name))
        if v is not None:
            company[key] = v

    for qp_name, key in (("SP vehicles", "total_vehicles"), ("SP drivers", "total_drivers"),
                         ("SP total commercial locations", "commercial_locations_count")):
        n = _to_number(_clean(fields.get(qp_name)))
        if n is not None:
            company[key] = n

    et = _one_of(fields, ENTITY_TYPE_BOXES)
    if et:
        company["entity_type"] = et
    lang = _one_of(fields, LANGUAGE_BOXES)
    if lang:
        company["language"] = lang

    for false_box, true_box, key in IDENTITY_BOOL:
        v = _bool_pair(fields, false_box, true_box)
        if v is not None:
            company[key] = v

    for key, ref in SAME_AS.items():
        v = company.get(key)
        if isinstance(v, str) and v.strip().lower() == "same":
            resolved = company.get(ref)
            if resolved:
                company[key] = resolved
            else:
                del company[key]
                warnings.append(f'company.{key} was "Same" on the QP but {ref} has no '
                                 f"value to copy from - left blank rather than storing \"Same\"")

    # On the real Lakeside QP this field holds a NOTE ("Willing to install"),
    # not a brand, and dash_cameras is False there - only trust it as a brand
    # when a camera is actually installed.
    brand = _clean(fields.get("SP dash cam brand"))
    if brand:
        if company.get("dash_cameras") is True:
            company["dash_cameras_brand"] = brand
        else:
            warnings.append(f'company.dash_cameras_brand: QP text {brand!r} not stored - '
                             f"dash_cameras is not Yes, so this reads as a note, not a brand")

    fein = company.get("fein")
    if fein and re.search(r"\d\.\d", fein):
        warnings.append(
            f"company.fein read as {fein!r} (dot-separated, exactly as printed on the QP) - "
            f"other parts of this pipeline use dash format (e.g. 12-3456789); preserved "
            f"verbatim per instructions, not auto-corrected. Confirm which is authoritative.")

    return company


# ---------------------------------------------------------------- section 2: coverages (page 2)

# Field names copied verbatim from the real QP's page 2 (and page 8, which
# repeats GKLL/Building/BPP for the per-location schedule - same shared
# fields, not re-read separately). The tiers available differ per coverage
# row exactly as printed on the form; "shorthand" marks the ones whose
# dossier convention is "100k"/"1m"-style text (they already have named
# dollar tiers) versus a plain number (the rest - see the module docstring).
COVERAGE_FIELDS = {
    "auto_liability": {"none": "SP Auto no coverage", "tiers": {"SP Auto 1M": "1m"},
                       "other": None, "amt": None, "shorthand": True},
    "total_stated_value": {"none": "SP SV no coverage", "tiers": {},
                           "other": "SP SV with coverage", "amt": "SP SV coverage requested",
                           "shorthand": False},
    "on_hook": {"none": "SP OnHook no coverage",
               "tiers": {"SP OnH 100k": "100k", "SP OnH 50k": "50k", "SP OnH 25k": "25k"},
               "other": "SP OnH with coverage", "amt": "SP OnH coverage requested",
               "shorthand": True},
    "general_liability": {"none": "SP GL no coverage", "tiers": {"SP GL 1M": "1m"},
                          "other": "SP GL with coverage", "amt": "SP GL coverage requested",
                          "shorthand": True},
    "garage_keepers": {"none": "SP GKLL no coverage",
                       "tiers": {"SP GKLL 150k": "150k", "SP GKLL 100k": "100k"},
                       "other": "SP GKLL with coverage", "amt": "SP GKLL coverage requested",
                       "shorthand": True},
    "building_property": {"none": "SP building no coverage", "tiers": {},
                          "other": "SP Building with coverage", "amt": "SP Building coverage requested",
                          "shorthand": False},
    "business_personal_property": {"none": "SP BPP no coverage", "tiers": {},
                                   "other": "SP BPP with coverage", "amt": "SP BPP coverage requested",
                                   "shorthand": False},
    "inland_marine": {"none": "SP IM no coverage", "tiers": {},
                      "other": "SP IM with coverage", "amt": "SP IM coverage requested",
                      "shorthand": False},
    # see the "SP UM" note in the module docstring
    "umbrella": {"none": "SP UM no coverage", "tiers": {},
                "other": "SP UM with coverage", "amt": "SP UM coverage requested",
                "shorthand": False},
}


def _coverage_value(fields: dict, spec: dict):
    if _is_on(fields.get(spec["none"])):
        return "none"
    for fname, tag in spec.get("tiers", {}).items():
        if _is_on(fields.get(fname)):
            return tag
    other = spec.get("other")
    if other and _is_on(fields.get(other)):
        amt = _clean(fields.get(spec["amt"])) if spec.get("amt") else None
        if amt is None:
            return None
        return _amount_to_k(amt) if spec.get("shorthand") else _to_number(amt)
    return None


def _workers_comp(fields: dict):
    if _is_on(fields.get("SP WC no coverage")):
        return {"status": "none"}
    if _is_on(fields.get("SP WC NPIF")):
        return {"status": "no_policy"}
    if _is_on(fields.get("SP WC with policy")):
        wc = {"status": "active"}
        exp = _clean(fields.get("SP WC policy expiration"))
        if exp:
            wc["expires"] = exp
        return wc
    return None


# ---------------------------------------------------------------- section 3: pct-group tables
# (revenue sources / operations / goods hauled / radius: all a uniform
# "SP {label} percentage/No/Yes" triple on the real QP)

REVENUE_SOURCES = {
    "private_party_incoming_calls": "private incoming calls",
    "motor_clubs": "motor clubs",
    "dealers_to_from_auctions": "dealers auctions",
    "salvage_hauling": "salvage hauling",
    "police_rotations": "police rotation",
    "impounds": "impounds",
    "logistics_companies": "logistics companies",
    "freight_brokers": "freight brokers",
    "other": "revenue other sources",
}

OPERATIONS = {
    "tow_disabled_autos": "tow disabled autos",
    "roadside_assistance": "roadside assistance",
    "private_property_impounds": "private impounds",
    "lien_sales": "lien sales",
    "police_impounds": "police impounds",
    "accident_recovery": "accident recovery",
    "used_auto_hauling": "used auto hauling",
    "salvage_auto_hauling": "salvage auto hauling",
    "new_auto_hauling": "new auto hauling",
    "hazmat_hauling": "HAZMAT hauling",
    "towing_not_for_hire": "tow not for hire",
    "repo_work": "any repo work",
    "garage_operations": "garage ops work",
    "mobile_auto_repair": "mobile auto repair work",
    "general_freight_hauling": "general freight hauling",
    "contractor_operations": "contractor operations",
    "refrigerated_produce_hauling": "refrigerated produce hauling",
    "non_refrigerated_produce_hauling": "non refer produce hauling",
    "other": "operations other ops",
}

GOODS_HAULED = {
    "private_passenger_vehicles": "private passenger vehs",
    "trucks_10k_gvw": "trucks 10k",
    "motorcycles": "motorcycles",
    "watercrafts": "watercrafts",
    "refrigerated_produce": "ref produce",
    "non_refrigerated_produce": "non ref prod",
    "general_dry_goods": "general dry goods",
    "hazmat_goods": "HAZMAT",
    "other": "other goods hauled",
}

RADIUS = {
    "lt50": "less 50 miles",
    "51_300": "less 300 miles",
    "301_500": "less 500 miles",
    "501_1000": "less 1000 miles",
    "1000_2500": "less 2500 miles",
    "2501_5000": "less 5000 miles",
    "5000_plus": "more than 5000 miles",
}

# The real QP has a genuine inconsistency (not a transcription slip here):
# the "less 50 miles" Yes/No checkboxes carry a verified extra leading space
# in their field names ("SP  less 50 miles Yes") that the percentage TEXT
# field for the same bucket does not ("SP less 50 miles percentage").
RADIUS_YESNO_OVERRIDE = {"lt50": " less 50 miles"}

OPS_DETAILS_PCT = {
    "allow_passengers": ("allow passengers during tow", "percentage"),
    "transportation_plates": ("transportation plates", "number"),
    "repossessed_plates": ("repossessed paltes", "percentage"),   # "paltes" verified exact on the real QP
}

OPS_DETAILS_BOOL = (
    ("SP hazardous cargo No", "SP hazardous cargo Yes", "hazardous_cargo_ever"),
    ("SP safety chains No", "SP safety chains Yes", "safety_chains_always"),
    ("SP written vehicle naintenance No", "SP written vehicle naintenance Yes", "written_vehicle_maintenance"),
    ("SP written safety program No", "SP written safety program Yes", "written_safety_program"),
)


def _ops_details(fields: dict) -> dict:
    out: dict = {}
    rev = _to_number(_clean(fields.get("Est Gross Revenue")))
    if rev is not None:
        out["gross_revenue"] = rev
    for key, (label, suffix) in OPS_DETAILS_PCT.items():
        v = _pct_group(fields, label, suffix)
        if v is not None:
            out[key] = v
    if _is_on(fields.get("SP less 12 hours")):
        out["hours"] = "lt12"
    elif _is_on(fields.get("SP more than 12 hours")):
        out["hours"] = "h12plus"
    for false_box, true_box, key in OPS_DETAILS_BOOL:
        v = _bool_pair(fields, false_box, true_box)
        if v is not None:
            out[key] = v
    return out


def _joined_rows(fields: dict, name_fn, count: int) -> str:
    parts = [_clean(fields.get(name_fn(i))) for i in range(1, count + 1)]
    return " ".join(p for p in parts if p).strip()


# ---------------------------------------------------------------- section 4: vehicles (pages 5-6)

def _vehicles(p5_widgets: list, p6_widgets: list) -> list[dict]:
    p5 = {w.field_name: _text(w.field_value) for w in p5_widgets}
    p6 = {w.field_name: _text(w.field_value) for w in p6_widgets}
    out = []
    for i in range(1, 21):
        info = _clean(p5.get(f"Veh info {i}"))
        svvin = _clean(p5.get(f"SP veh SV and VIN {i}"))
        if not info and not svvin:
            continue
        v: dict = {}
        if info:
            parts = re.split(r"\s{2,}", info.strip())
            if parts:
                ym = parts[0].strip().split(" ", 1)
                if ym and re.fullmatch(r"(19|20)\d{2}", ym[0]):
                    v["year"] = int(ym[0])
                    if len(ym) > 1 and ym[1].strip():
                        v["maker"] = ym[1].strip()
            if len(parts) > 1 and parts[1].strip():
                v["body_type"] = parts[1].strip()
            if len(parts) > 2:
                gvw = _to_number(parts[2])
                if gvw is not None:
                    v["gvw"] = gvw
            if len(parts) > 3:
                mt = _to_number(parts[3])
                if mt is not None:
                    v["max_towed"] = mt
        if svvin:
            parts = re.split(r"\s{2,}", svvin.strip())
            if parts:
                sv = _to_number(parts[0])
                if sv is not None:
                    v["stated_value"] = sv
            if len(parts) > 1 and parts[1].strip():
                v["vin"] = parts[1].strip()
        # Per-vehicle on-hook: only the typed custom amount is trusted (see
        # module docstring - the 5 tier checkboxes here are "undefined_NNN"
        # with no name to key off, so they are not interpreted at all).
        amt = _clean(p6.get(f"Text8.{i - 1} {i}"))
        if amt:
            v["onhook"] = _amount_to_k(amt)
        if v:
            out.append(v)
    return out


# ---------------------------------------------------------------- section 5: drivers (page 7)

def _drivers(p7_widgets: list) -> list[dict]:
    by_row: dict[int, dict[str, str]] = {}
    for w in p7_widgets:
        m = re.search(r"Row(\d+)$", w.field_name)
        if not m:
            continue
        row = int(m.group(1))
        slot = "name" if "name" in w.field_name.lower() else "detail"
        val = _text(w.field_value)
        if val:
            by_row.setdefault(row, {})[slot] = val

    out = []
    for row in sorted(by_row):
        name = by_row[row].get("name")
        if not name:
            continue
        drv: dict = {"name": name}
        detail = by_row[row].get("detail")
        if detail:
            # STATE  LICENSE  BIRTHDAY  POSITION  YEARS_EXP  [DATE_OF_HIRE]
            parts = re.split(r"\s{2,}", detail.strip())
            if len(parts) > 0 and re.fullmatch(r"[A-Za-z]{2}", parts[0].strip()):
                drv["state"] = parts[0].strip().upper()
            if len(parts) > 1 and parts[1].strip():
                drv["license"] = parts[1].strip()
            if len(parts) > 2 and parts[2].strip():
                drv["birthday"] = parts[2].strip()
            if len(parts) > 3 and parts[3].strip():
                drv["position"] = parts[3].strip()
            if len(parts) > 4:
                yrs = _to_number(parts[4])
                if yrs is not None:
                    drv["years_experience"] = yrs
            if len(parts) > 5 and parts[5].strip():
                drv["date_of_hire"] = parts[5].strip()
        out.append(drv)
    return out


# ---------------------------------------------------------------- section 6: loss runs (page 9)

def _loss_runs(p9_widgets: list) -> tuple[list[dict], list[str]]:
    """5 positional blocks of 7 widgets: (carrier, no-policy checkbox, policy
    number, effective dates, premium, 2 unlabeled checkboxes). See the module
    docstring for why this is positional and why the year comes from the
    effective dates, never from the block's field-name label."""
    warnings: list[str] = []
    n_blocks = len(p9_widgets) // 7
    if n_blocks == 0:
        return [], warnings
    if n_blocks != 5:
        warnings.append(f"page 9 (loss runs) has {len(p9_widgets)} widgets, expected 35 "
                        f"(5 blocks x 7) plus signature/code - parsing {n_blocks} block(s), "
                        f"verify the loss-run section by hand")
    out = []
    for b in range(n_blocks):
        chunk = p9_widgets[b * 7:(b + 1) * 7]
        carrier = _clean(chunk[0].field_value)
        no_policy = _is_on(chunk[1].field_value)
        policy_number = _clean(chunk[2].field_value)
        eff = _clean(chunk[3].field_value)
        premium = _clean(chunk[4].field_value)
        if not any([carrier, no_policy, policy_number, eff, premium]):
            continue
        year = _year_from_effective_dates(eff) if eff else None
        if year is None:
            warnings.append(f"loss-run block {b + 1}: could not derive a policy year from "
                            f"effective dates {eff!r} - block skipped rather than guessed")
            continue
        run: dict = {"year": year}
        if no_policy:
            run["no_policy"] = True
        if carrier:
            run["carrier"] = carrier
        if policy_number:
            run["policy_number"] = policy_number
        if eff:
            run["effective_dates"] = eff
        if premium:
            n = _to_number(premium)
            if n is not None:
                run["premium"] = n
        out.append(run)
    return out, warnings


# ---------------------------------------------------------------- section 7: contracts, location, meta

def _contracts(fields: dict) -> list[dict]:
    out = []
    for i in range(1, 5):
        name = _clean(fields.get(f"SP largest client {i}"))
        if not name:
            continue
        c: dict = {"name": name}
        pct = _to_number(_clean(fields.get(f"SP largest client {i} percentage")))
        if pct is not None:
            c["pct"] = pct
        out.append(c)
    return out


def _location(fields: dict) -> dict:
    """Page 8's per-location schedule. Only the plainly-named fields are
    read; dogs/video/alarm/keys/property-return/weapons/relinquish policies
    are backed by "undefined_NNN" checkboxes with no confirmed semantics on
    the real QP (every one of them was blank on Lakeside, so there was nothing
    to corroborate a guess against either) and are left unmapped."""
    loc: dict = {}
    addr = _clean(fields.get("Locat on fu address_2"))
    if addr:
        loc["address"] = addr
    hb = _bool_pair(fields, "SP not homebased", "SP homebased")
    if hb is not None:
        loc["home_based"] = hb
    cln = _to_number(_clean(fields.get("Commercial ocation number")))
    if cln is not None:
        loc["commercial_location_number"] = cln
    for key in ("garage_keepers", "building_property", "business_personal_property"):
        v = _coverage_value(fields, COVERAGE_FIELDS[key])
        if v is not None:
            loc[key] = v
    for key, name in (
        ("avg_vehicles_stored", "Average  of veh cles stored"),
        ("max_vehicles_stored", "Max  of veh cles stored"),
        ("outdoor_sq_footage", "Outdoor storage sq footage"),
        ("indoor_sq_footage", "Indoor storage square footage"),
        ("office_sq_footage", "Office square footage"),
    ):
        n = _to_number(_clean(fields.get(name)))
        if n is not None:
            loc[key] = n
    details = _joined_rows(fields, lambda i: f"Locat on detai sRow{i}", 3)
    if details:
        loc["details"] = details
    return loc


def _meta_data(fields: dict) -> dict:
    """Only meta_data.interstate is read: it is the one meta_data key the
    rest of the pipeline (rts_fill.py's Federal Filing Required row) actually
    consumes. Page 17 has several more fields (SAFER add date, policy
    cancellation date, motor vehicle/cargo/authorized-for-hire flags, ...)
    with no existing home in the dossier schema - left out rather than
    inventing new schema shape for them (see this change's final report)."""
    out: dict = {}
    interstate = _clean(fields.get("Interstate"))
    if interstate is not None:
        out["interstate"] = interstate
    return out


# ---------------------------------------------------------------- top level


def read_qp(pdf_path) -> dict:
    """Read a Sierra Pacific QP PDF into {"dossier": {...}, "warnings": [...]}.

    `dossier` is shaped like clients/<slug>/state.json (see
    app-form/config/client_data.example.json for the documented schema) -
    only keys/sections actually found in the PDF are present, so it merges
    cleanly (see merge_into_dossier). `warnings` covers things worth a human
    look: unresolved "Same" placeholders, a mismatched loss-run block count,
    the FEIN's dot-vs-dash format, a dash-cam brand that reads as a note.

    Raises QPReadError for anything that is not readable as a QP - a missing
    file, a non-PDF file, or a PDF with no (or almost no) AcroForm widgets.
    Never raises for merely unusual/sparse data; absent sections just do not
    appear in the output.
    """
    pages = _load_widgets(pdf_path)
    if len(pages) < 3:
        raise QPReadError(
            f"'{pdf_path}' does not look like a Sierra Pacific QP - only "
            f"{len(pages)} page(s) carry AcroForm form fields (a real QP has "
            f"the CAP application on pages 1-9 plus a few more).")

    fields = _flat_fields(pages)
    warnings: list[str] = []
    dossier: dict = {"company": _identity(fields, warnings)}

    code = _clean(fields.get("SP code"))
    if code:
        dossier["sp_policy_code"] = code
    source = _clean(fields.get("Source code"))
    if source:
        dossier["source_code"] = source
    sig = _clean(fields.get("Insured name  s gnature  date"))
    if sig:
        dossier["insured_signature"] = sig

    coverages = {k: _coverage_value(fields, spec) for k, spec in COVERAGE_FIELDS.items()}
    coverages = {k: v for k, v in coverages.items() if v is not None}
    wc = _workers_comp(fields)
    if wc:
        coverages["workers_comp"] = wc
    if coverages:
        dossier["coverages"] = coverages

    other_cov = _joined_rows(fields, lambda i: f"SP Other coverages requestedRow{i}", 3)
    if other_cov:
        dossier["other_coverages"] = other_cov

    revenue = {k: v for k, lbl in REVENUE_SOURCES.items() if (v := _pct_group(fields, lbl)) is not None}
    if revenue:
        dossier["revenue_sources"] = revenue
    rev_other = _joined_rows(fields, lambda i: f"SP Describe other revenue sourcesRow{i}", 3)
    if rev_other:
        dossier["revenue_other_desc"] = rev_other

    contracts = _contracts(fields)
    if contracts:
        dossier["contracts"] = contracts

    operations = {k: v for k, lbl in OPERATIONS.items() if (v := _pct_group(fields, lbl)) is not None}
    if operations:
        dossier["operations"] = operations
    ops_other = _joined_rows(fields, lambda i: f"SP Describe other operationsRow{i}", 4)
    if ops_other:
        dossier["operations_other_desc"] = ops_other

    goods = {k: v for k, lbl in GOODS_HAULED.items() if (v := _pct_group(fields, lbl)) is not None}
    if goods:
        dossier["goods_hauled"] = goods
    goods_other = _joined_rows(fields, lambda i: f"SP Describe other good hau edRow{i}", 5)
    if goods_other:
        dossier["goods_other_desc"] = goods_other

    ops_details = _ops_details(fields)
    if ops_details:
        dossier["ops_details"] = ops_details

    radius = {k: v for k, lbl in RADIUS.items()
              if (v := _radius_group(fields, lbl, RADIUS_YESNO_OVERRIDE.get(k))) is not None}
    if radius:
        dossier["radius"] = radius

    desc = _joined_rows(fields, lambda i: f"SP Overal descript on of company operat onsRow{i}", 14)
    if desc:
        dossier["overall_description"] = desc

    vehicles = _vehicles(pages.get(5, []), pages.get(6, []))
    if vehicles:
        dossier["vehicles"] = vehicles
        totals: dict = {"power_units": len(vehicles)}
        trailer_n = _to_number(_clean(fields.get("SP trailer")))
        if trailer_n is not None:
            totals["trailers"] = trailer_n
        sv = coverages.get("total_stated_value")
        if isinstance(sv, (int, float)):
            totals["stated_value"] = sv
        dossier["vehicle_totals"] = totals

    drivers = _drivers(pages.get(7, []))
    if drivers:
        dossier["drivers"] = drivers
    oec = _to_number(_clean(fields.get("Total number of other employees_2")))
    if oec is not None:
        dossier["other_employees_count"] = oec

    loss_runs, lr_warnings = _loss_runs(pages.get(9, []))
    warnings.extend(lr_warnings)
    if loss_runs:
        dossier["loss_runs"] = loss_runs

    location = _location(fields)
    if location:
        dossier["location"] = location

    meta = _meta_data(fields)
    if meta:
        dossier["meta_data"] = meta

    return {"dossier": dossier, "warnings": warnings}


ROW_KEYS = ("vin", "name", "year")

_ANNOTATION = re.compile(r"\s*\([^)]*\)\s*$")
_DIGITS = re.compile(r"\D")
_RANGE_SPLIT = re.compile(r"\s*(?:-|–|to|through)\s*", re.I)


def _as_number(text: str):
    """50000, '50,000', '$50,000', '20' -> a number. None if it isn't one."""
    cleaned = text.replace(",", "").replace("$", "").strip()
    try:
        return float(cleaned)
    except ValueError:
        return None


def _same_dates(a: str, b: str) -> bool:
    """True when both sides name the same date, or the same range of dates.

    The dossier holds legacy '08/03/2023 - 08/03/2024' while the QP prints
    '8.3.23 - 8.3.24'. Identical information, and a broker asked to adjudicate
    that difference learns to stop reading the list.
    """
    left, right = _RANGE_SPLIT.split(a), _RANGE_SPLIT.split(b)
    if len(left) != len(right):
        return False
    out = []
    for x, y in zip(left, right):
        dx, dy = format_date(x.strip()), format_date(y.strip())
        if dx is None or dy is None:
            return False
        out.append(dx == dy)
    return all(out)


def _equivalent(base, new) -> bool:
    """Do these two values carry the same information?

    Only a real difference is worth a broker's attention. Formatting variants
    are not disagreements: the same phone with dots instead of dashes, the same
    FEIN, the same date in another notation, a different capitalisation, a
    number that arrived as text, or a value the QP annotated in parentheses
    ('20 (200mi max)' is still 20).
    """
    if base == new:
        return True
    a, b = str(base).strip(), str(new).strip()
    if a.casefold() == b.casefold():
        return True

    # A trailing parenthetical is an annotation, not a different value.
    a_core, b_core = _ANNOTATION.sub("", a), _ANNOTATION.sub("", b)
    if a_core.casefold() == b_core.casefold():
        return True

    na, nb = _as_number(a_core), _as_number(b_core)
    if na is not None and nb is not None:
        return na == nb

    pa, pb = format_phone(a), format_phone(b)
    if pa and pb and pa == pb:
        return True

    # Identifier-shaped values (FEIN, licence, CA number): same digits, and the
    # same count of them, so 12-3456789 and 12.3456789 match but 1-23456789 in
    # a field that genuinely differs still does not collapse to equal.
    da, db = _DIGITS.sub("", a), _DIGITS.sub("", b)
    if da and da == db and not (a_core.isalpha() or b_core.isalpha()):
        return True

    return _same_dates(a, b)


def _row_key(item: dict) -> tuple[str, str] | None:
    for k in ROW_KEYS:
        if item.get(k):
            return k, str(item[k]).lower()
    return None


def _merge(base, new, path: str, conflicts: list[str]):
    """Gap-fill merge: the dossier wins, the QP only supplies what is missing.

    Deliberately NOT deep_merge's "new wins". A QP is a point-in-time snapshot,
    so a QP produced before a broker's correction would put the stale figure
    back with nothing to show for it — on the real Lakeside file the QP still
    says a stated value of 50,000 while the corrected dossier says 55,000.
    Disagreements are collected and handed back as questions instead, which is
    the standing rule: the automation drafts and flags, a broker decides.
    """
    if isinstance(base, dict) and isinstance(new, dict):
        out = dict(base)
        for k, v in new.items():
            sub = f"{path}.{k}" if path else k
            out[k] = _merge(base[k], v, sub, conflicts) if k in base else v
        return out

    if isinstance(base, list) and isinstance(new, list):
        out = list(base)
        for item in new:
            key = _row_key(item) if isinstance(item, dict) else None
            if not key:
                out.append(item)
                continue
            field, value = key
            for i, old in enumerate(out):
                if isinstance(old, dict) and str(old.get(field, "")).lower() == value:
                    out[i] = _merge(old, item, f"{path}[{value}]", conflicts)
                    break
            else:
                out.append(item)
        return out

    # Scalars. An empty QP field means "the form was blank", not "erase this".
    if new in (None, ""):
        return base
    if base in (None, ""):
        return new
    if not _equivalent(base, new):
        conflicts.append(f"{path}: on file {base!r}, QP says {new!r} — kept {base!r}")
    return base


def merge_into_dossier(dossier: dict, qp_data: dict) -> tuple[dict, list[str]]:
    """Layer QP data under an existing dossier. Returns (merged, conflicts).

    Conflicts are human-readable one-liners naming the field and both values,
    meant to be shown to the broker who then decides which is right.
    """
    conflicts: list[str] = []
    merged = _merge(dossier, qp_data, "", conflicts)
    return merged, conflicts


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__, file=sys.stderr)
        return 2
    try:
        result = read_qp(sys.argv[1])
    except QPReadError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result["dossier"], indent=1))
    if result["warnings"]:
        print("\nwarnings:", file=sys.stderr)
        for w in result["warnings"]:
            print(f"  - {w}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
