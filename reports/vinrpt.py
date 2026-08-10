r"""VIN report — a capture of the government decoder's own results page.

JC's rule, learned the hard way after this engine once rendered VIN data onto a
blank letterhead: a report page is EVIDENCE, and evidence is a picture of the
page, "I want to see the fucking page." So each vehicle gets a screenshot of
NHTSA vPIC's public decoder with the VIN typed in, one vehicle after another,
plus the API data pulled once for the compare-and-question pass (never silent
corrections — questions for the broker).

A 16-character VIN is repaired when what is missing is the check digit (the one
character mathematically derived from the others — see vin_checkdigit.py). The
repaired VIN is proposed as a QUESTION with its NHTSA decode; it is never
written into the app on our own authority.

Emits:
  <SP> VIN report <M.D.YY>.pdf    one capture per vehicle, stacked
  vin_reports.json                data + questions for the checklist/UW pages
"""
from __future__ import annotations

import argparse
import datetime
import json
import re
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CLIENTS = ROOT / "app-form" / "clients"
SHOTS = ROOT / "reports" / "captures"
sys.path.insert(0, str(ROOT / "reports"))
sys.path.insert(0, str(ROOT / "watcher"))

DECODER = "https://vpic.nhtsa.dot.gov/decoder/"
API = "https://vpic.nhtsa.dot.gov/api/vehicles/DecodeVinValues/{vin}?format=json"


def capture_decoder(vin: str, png: Path, expect: str = "") -> str:
    """Type the VIN into NHTSA's own decoder and photograph the results.

    There is no GET URL for a decode — the form posts — so the capture drives the
    page: fill `#VIN`, click `Decode VIN`, wait for the results table. Returns ''
    on success or the reason it failed. A page that reads as an error is a
    failure, never a report page: this module once shipped a 404 into the packet
    because it only checked that a screenshot existed.
    """
    from playwright.sync_api import sync_playwright
    png.parent.mkdir(parents=True, exist_ok=True)
    try:
        with sync_playwright() as p:
            b = p.chromium.launch()
            pg = b.new_context(viewport={"width": 1280, "height": 1400},
                               locale="en-US").new_page()
            # The results URL 404s when hit cold — the decode only runs off the
            # form's own submit, so drive the form exactly like a person would.
            pg.goto(DECODER, timeout=60_000, wait_until="domcontentloaded")
            pg.wait_for_timeout(1_500)
            pg.fill("#VIN", vin)
            pg.click("#btnSubmit")
            # The results page shows the decode (make, model, specs) but does not
            # echo the VIN back into the body — checking for the VIN string was a
            # false negative on every run. The API already told us what this VIN
            # is, so the page proving itself means showing that same make.
            want = (expect or "").lower() or "decoded"
            body = ""
            for _ in range(5):                      # results render within ~3-12s
                pg.wait_for_timeout(3_000)
                body = pg.inner_text("body") if pg.locator("body").count() else ""
                if want in body.lower():
                    break
            low = body.lower()
            if "error." in low or "does not exist" in low:
                b.close()
                return "the decoder returned an error page"
            if want not in low:
                b.close()
                return (f"the results page never showed “{expect or 'decoded'}” — "
                        f"decode did not run")
            pg.screenshot(path=str(png), full_page=True)
            b.close()
        return ""
    except Exception as exc:  # noqa: BLE001
        return f"{type(exc).__name__}: {str(exc)[:120]}"


def api_decode(vin: str) -> dict:
    try:
        with urllib.request.urlopen(API.format(vin=vin), timeout=45) as r:
            return json.loads(r.read())["Results"][0]
    except Exception as exc:  # noqa: BLE001
        return {"ErrorText": f"lookup failed: {exc}"}


def compare(declared: dict, rec: dict) -> list[str]:
    """App vs NHTSA — questions only, never rewrites."""
    qs: list[str] = []
    vin = declared.get("vin", "")
    err = (rec.get("ErrorText") or "").strip()
    if err and not err.startswith("0 -"):
        qs.append(f"VIN {vin}: NHTSA reports “{err.split(';')[0]}” — confirm the VIN "
                  f"with the insured before submission.")
    dy, ny = str(declared.get("year") or ""), str(rec.get("ModelYear") or "")
    if dy and ny and dy != ny:
        qs.append(f"VIN {vin}: app says model year {dy}, NHTSA decodes {ny} — "
                  f"which is correct?")
    dm, nm = str(declared.get("maker") or "").lower(), str(rec.get("Make") or "").lower()
    if dm and nm and dm.split()[0] not in nm and nm.split()[0] not in dm:
        qs.append(f"VIN {vin}: app says make “{declared.get('maker')}”, NHTSA "
                  f"decodes “{rec.get('Make')}” — confirm the unit.")
    gvw = re.sub(r"\D", "", str(declared.get("gvw") or ""))
    gclass = rec.get("GVWR") or ""
    nums = [int(n.replace(",", "")) for n in re.findall(r"[\d,]{4,}", gclass)]
    if gvw.isdigit() and len(nums) >= 2 and not (nums[0] <= int(gvw) <= nums[1]):
        qs.append(f"VIN {vin}: app states GVW {int(gvw):,} lb but NHTSA puts this "
                  f"chassis in “{gclass}” — verify the weight rating.")
    return qs


def run(slug: str, to_drive: bool = False) -> dict:
    from pagebuild import capture_to_pdf
    import fitz
    from vin_checkdigit import repair_missing_check_digit

    folder = CLIENTS / slug
    dossier = json.loads((folder / "state.json").read_text(encoding="utf-8"))
    sp = dossier.get("sp_code") or "CLIENT"
    vehicles = dossier.get("vehicles") or []
    today = datetime.date.today()
    stamp = f"{today.month}.{today.day}.{str(today.year)[2:]}"

    out = ROOT / "reports" / "out" / slug
    out.mkdir(parents=True, exist_ok=True)
    pdf_name = f"{sp} CAP VIN report {stamp}.pdf"

    questions: list[str] = []
    records: list[dict] = []
    pages = fitz.open()

    for i, v in enumerate(vehicles, 1):
        vin = str(v.get("vin") or "").strip().upper()
        if not vin:
            questions.append(f"Vehicle {i} ({v.get('year','')} {v.get('maker','')}) "
                             f"has no VIN on file — one VIN report per vehicle is a "
                             f"submission requirement.")
            continue
        if len(vin) == 16:
            rep = repair_missing_check_digit(vin)
            if rep["ok"]:
                cand = rep["vin"]
                rec = api_decode(cand)
                clean = (rec.get("ErrorText") or "").startswith("0 -")
                questions.append(
                    f"VIN on file “{vin}” has 16 characters (a VIN has 17). The "
                    f"check digit is the only character that can be derived, and "
                    f"exactly one value satisfies it: “{cand}”"
                    + (f", which NHTSA decodes clean as {rec.get('ModelYear','?')} "
                       f"{rec.get('Make','?')} {rec.get('Series') or rec.get('Model','')}"
                       if clean else "")
                    + ". Confirm against the title or registration before using it.")
                vin = cand              # capture the candidate page as evidence
            else:
                questions.append(f"VIN on file “{vin}” has 16 characters and cannot "
                                 f"be repaired automatically ({rep['reason']}) — get "
                                 f"the full VIN from the title.")
                continue
        elif len(vin) != 17:
            questions.append(f"VIN on file “{vin}” has {len(vin)} characters (a VIN "
                             f"has 17) — get the corrected VIN from the insured.")
            continue

        rec = api_decode(vin)
        records.append({"vin": vin, "declared": v,
                        "decoded": {k: rec.get(k) for k in
                                    ("ModelYear", "Make", "Model", "Series",
                                     "BodyClass", "GVWR", "VehicleType")}})
        questions.extend(compare({**v, "vin": vin}, rec))

        png = SHOTS / f"{sp}_vin_{i}.png"
        err = capture_decoder(vin, png, expect=str(rec.get("Make") or ""))
        if err or not png.exists():
            questions.append(f"VIN {vin}: the decoder page could not be captured "
                             f"({err or 'no image'}) — capture it by hand.")
            continue
        tmp = out / f".vin_{i}.pdf"
        capture_to_pdf(png, tmp, f"VIN {vin}",
                       "vpic.nhtsa.dot.gov — NHTSA VIN decoder")
        with fitz.open(tmp) as d:
            pages.insert_pdf(d)
        tmp.unlink(missing_ok=True)
        png.unlink(missing_ok=True)

    made, n_pages = None, len(pages)
    if n_pages:
        pages.save(out / pdf_name)
        made = str(out / pdf_name)
    pages.close()

    (out / "vin_reports.json").write_text(json.dumps(
        {"retrieved": datetime.datetime.now().isoformat(timespec="seconds"),
         "source": "NHTSA vPIC", "records": records, "questions": questions},
        indent=1), encoding="utf-8")
    return {"ok": True, "pdf": made, "vehicles": len(vehicles),
            "captured": n_pages, "questions": questions}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--client", required=True)
    args = ap.parse_args()
    r = run(args.client)
    print(f"VIN report — {r['vehicles']} vehiculo(s), {r['captured']} pagina(s)")
    if r["pdf"]:
        print(f"  PDF: {r['pdf']}")
    for q in r["questions"]:
        print(f"  · {q}")


if __name__ == "__main__":
    main()
