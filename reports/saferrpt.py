r"""FMCSA SAFER Company Snapshot report — the federal record, captured as-is.

Pages 16-18 of the real Lakeside QP are this: safer.fmcsa.dot.gov's Company
Snapshot for the carrier's USDOT number, captured full-page and sliced across
sheets. It is the underwriter's authority on fleet size, operating status and
federal insurance-on-file, so it is kept as the page itself, never rebuilt —
exactly like the web reports.

Two facts decide where this can run, and both are surfaced as a clean problem
rather than a crash on a machine that cannot meet them:
  * FMCSA answers 403 to any request from outside the US — this needs a US IP.
  * The capture reuses webrpt's browser path, so it needs Playwright + Chromium.

Emits:
  <SP> CAP SAFER report <M.D.YY>.pdf   the Company Snapshot, sliced to letter
  safer_report.json                    parsed fields + questions
"""
from __future__ import annotations

import argparse
import datetime
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CLIENTS = ROOT / "app-form" / "clients"
SHOTS = ROOT / "reports" / "captures"

SNAPSHOT = ("https://safer.fmcsa.dot.gov/query.asp?searchtype=ANY"
            "&query_type=queryCarrierSnapshot&query_param=USDOT"
            "&query_string={dot}")


def snapshot_url(usdot: str) -> str:
    return SNAPSHOT.format(dot=re.sub(r"\D", "", str(usdot or "")))


def _int(text: str):
    m = re.search(r"\d[\d,]*", str(text or ""))
    return int(m.group(0).replace(",", "")) if m else None


def parse(text: str) -> dict:
    """Pull the fields the underwriter reads off the Company Snapshot.

    The page is a labelled table; a browser's inner_text renders each row as
    "Label: value", so the fields come off with a label-anchored search rather
    than positional guessing. A field the page omits stays absent — never a
    zero or an empty string that would read as a real answer.
    """
    def field(label: str):
        m = re.search(rf"{label}\s*:?\s*([^\n]+)", text, re.I)
        return m.group(1).strip() if m else ""

    f: dict = {"source": "safer.fmcsa.dot.gov (FMCSA Company Snapshot)"}
    status = field("USDOT Status")
    if status:
        f["usdot_status"] = status.split()[0].upper()
    name = field("Legal Name")
    if name:
        f["legal_name"] = name
    oos = field("Out of Service Date")
    if oos:
        f["out_of_service"] = oos.strip().lower() not in ("none", "")
    mcs = field("MCS-150 Form Date")
    if mcs:
        f["mcs150_date"] = mcs
    auth = field("Operating Authority Status")
    if auth:
        f["operating_authority"] = auth
    pu = _int(field("Power Units"))
    if pu is not None:
        f["power_units"] = pu
    dr = _int(field("Drivers"))
    if dr is not None:
        f["drivers"] = dr
    op = field("Carrier Operation")
    if op:
        f["carrier_operation"] = op
    phone = field("Phone")
    if phone:
        f["phone"] = phone
    return f


def _norm(s: str) -> str:
    s = re.sub(r"[^a-z0-9 ]", " ", str(s or "").lower())
    drop = {"llc", "inc", "co", "company", "the", "towing", "recovery",
            "transport", "trucking", "services", "service"}
    return " ".join(w for w in s.split() if w not in drop)


def compare(dossier: dict, f: dict) -> list[str]:
    """Questions, never rewrites — the same contract every report holds to."""
    qs: list[str] = []
    c = dossier.get("company", {}) or {}

    if f.get("usdot_status") and f["usdot_status"] != "ACTIVE":
        qs.append(f"FMCSA shows this USDOT as {f['usdot_status']}, not ACTIVE — an "
                  f"inactive carrier cannot bind; confirm the number and status.")
    if f.get("out_of_service"):
        qs.append("FMCSA has an out-of-service order on this carrier — confirm it "
                  "is resolved before this goes out.")

    app_name, safer_name = c.get("first_named_insured", ""), f.get("legal_name", "")
    if app_name and safer_name and _norm(app_name) != _norm(safer_name):
        qs.append(f"Legal name: app says “{app_name}”, FMCSA has "
                  f"“{safer_name}” — confirm the named insured.")

    app_units = c.get("total_vehicles")
    safer_units = f.get("power_units")
    if isinstance(app_units, int) and isinstance(safer_units, int) and app_units != safer_units:
        qs.append(f"Power units: the app lists {app_units}, FMCSA's authoritative "
                  f"count is {safer_units} — which is the real fleet size? "
                  f"MCS-150 may be stale, or a truck is missing from the schedule.")
    return qs


def run(slug: str, usdot: str | None = None) -> dict:
    folder = CLIENTS / slug
    dossier = json.loads((folder / "state.json").read_text(encoding="utf-8"))
    c = dossier.get("company", {}) or {}
    sp = dossier.get("sp_code") or "CLIENT"
    usdot = usdot or c.get("usdot_number") or ""
    if not re.sub(r"\D", "", str(usdot)):
        return {"ok": False, "error": "no USDOT number on the app"}

    today = datetime.date.today()
    stamp = f"{today.month}.{today.day}.{str(today.year)[2:]}"
    out = ROOT / "reports" / "out" / slug
    out.mkdir(parents=True, exist_ok=True)
    SHOTS.mkdir(parents=True, exist_ok=True)
    png = SHOTS / f"{sp}_safer.png"
    pdf = out / f"{sp} CAP SAFER report {stamp}.pdf"

    # Reuse webrpt's capture: one tested browser path, launched the same way,
    # with the same block-and-relevance guards behind pagebuild.
    sys.path.insert(0, str(ROOT / "reports"))
    import webrpt
    from pagebuild import CaptureRejected, capture_to_pdf
    text, err = webrpt.capture(snapshot_url(usdot), png, full_page=True, mark=False)
    if err or not png.exists():
        return {"ok": False, "error": err or "the SAFER page did not capture "
                "(FMCSA is 403 outside the US, and the capture needs Playwright)"}
    dot_digits = re.sub(r"\D", "", str(usdot))
    title = f"USDOT {dot_digits} — {c.get('first_named_insured') or slug}"
    attribution = f"safer.fmcsa.dot.gov · {datetime.datetime.now():%Y-%m-%d %H:%M}"
    try:
        pages = capture_to_pdf(png, pdf, title, attribution, page_text=text)
    except CaptureRejected as exc:
        return {"ok": False, "error": f"capture rejected: {exc}"}

    rec = parse(text)
    questions = compare(dossier, rec)
    (out / "safer_report.json").write_text(json.dumps(
        {"retrieved": datetime.datetime.now().isoformat(timespec="seconds"),
         "fields": rec, "questions": questions}, indent=1), encoding="utf-8")
    return {"ok": True, "pdf": str(pdf), "pages": pages,
            "record": rec, "questions": questions}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--client", required=True)
    ap.add_argument("--dot")
    args = ap.parse_args()
    r = run(args.client, args.dot)
    if not r["ok"]:
        print("ERROR:", r["error"])
        return
    print(f"SAFER — {r['pdf']} ({r['pages']} pages)")
    for k, v in r["record"].items():
        print(f"  {k}: {v}")
    for q in r["questions"]:
        print(f"  · {q}")


if __name__ == "__main__":
    main()
