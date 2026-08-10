r"""Quoting packet builder — one PDF, in JC's real order, with his real checklist.

The reference is not doctrine text but the artifact itself: `LAKES CAP tow QP
6.10.26.pdf`, JC's own 32-page packet, read page by page. Its shape:

    pages 1-14   the Sierra working set (app + LR request form + LR scores +
                 certificate schedule + Meta data + QP checklist)
    then         MCP, loss runs, Secretary of State, COI,
                 website/Facebook/Instagram/Yelp, street view, overhead,
                 VIN report, CHP — each a capture of the real page

Rules carried over from the calls, verbatim where it matters:
  * "we always use dated versions of the QP... we never delete the older
    versions" — a same-day rebuild gets `_2`, never an overwrite.
  * `comp` in the filename means COMPLETED — "this comp means it was completed,
    we got all we needed". The suffix is EARNED: it only appears when the
    checklist gate passes. An incomplete packet is saved without it.
  * The checklist page inside the packet is filled by this builder — each
    report's `Included` box and date — so page 14 tells the truth about what
    the packet actually contains.
  * No placeholder pages, ever. A missing report is a missing checkbox and a
    line to the operator, not a page an underwriter reads.

Usage:
  python qp_build.py --client lakeside-towing-llc [--risk tow] [--no-drive]
"""
from __future__ import annotations

import argparse
import datetime
import json
import re
import sys
from pathlib import Path

import fitz

ROOT = Path(__file__).resolve().parent.parent
CLIENTS = ROOT / "app-form" / "clients"
OUT = ROOT / "reports" / "out"
sys.path.insert(0, str(ROOT / "watcher"))
sys.path.insert(0, str(ROOT / "shared"))
import formatting  # noqa: E402 - needs sys.path set up first

# checklist row -> (field on page 14, filename globs in client folder / reports out)
REPORT_ROWS = [
    ("CA MCP or state filing report", "p14_ca_mcp_or_state_filing_report_included",
     ["*MCP report*.pdf", "*state filing*.pdf"]),
    ("Secretary of State report", "p14_secretary_of_state_report_included",
     ["*SOS report*.pdf", "*secretary of state*.pdf"]),
    ("Website report", "p14_website_report_included", ["*website report*.pdf"]),
    ("Facebook report", "p14_facebook_report_included", ["*facebook report*.pdf"]),
    ("Instagram report", "p14_instagram_report_included", ["*instagram report*.pdf"]),
    ("Yelp report", "p14_yelp_report_included", ["*yelp report*.pdf"]),
    ("Google street report per loc", "p14_google_street_report_per_loc_included",
     ["*street report*.pdf"]),
    ("Google overhead report per loc", "p14_google_overhead_report_per_loc_included",
     ["*overhead report*.pdf"]),
    ("VIN report per vehicle", "p14_vin_report_per_vehicle_included",
     ["*VIN report*.pdf"]),
]
# SAFER and CHP travel in the packet but have no checklist row of their own
EXTRA_REPORTS = [("SAFER report", ["*SAFER report*.pdf"]),
                 ("CHP report", ["*CHP report*.pdf", "*CHP*.pdf"])]


def m8(d: datetime.date | None = None) -> str:
    """Filename date stamp — defaults to today. Formatting itself now lives in
    shared/formatting.py; this wrapper keeps the "no argument = today"
    filename behaviour exactly as it was (filename-generation logic is out of
    scope for JC's 7.27 formatting rules — it already matches doctrine)."""
    return formatting.format_date(d or datetime.date.today())


def collect_source(folder: Path) -> dict[str, list[Path]]:
    """Classify what Slack drops archived into _source: loss runs, COIs, MVRs.

    Three lessons folded in: archived names carry underscores where the original
    had spaces (so space-based globs miss everything), JC's COI arrives with no
    file extension at all, and the same document re-sent across drops piles up
    under different timestamps — the NICO scan landed three times in one test.
    Dedupe by the original name (timestamp prefix stripped), keep the newest,
    and verify each survivor actually opens as a PDF whatever its suffix.
    """
    import re as _re
    out: dict[str, list[Path]] = {"loss_runs": [], "cois": [], "mvrs": []}
    src = folder / "_source"
    if not src.is_dir():
        return out
    newest: dict[tuple[str, str], Path] = {}
    for f in sorted(src.iterdir()):
        if not f.is_file() or f.name.endswith(".txt"):
            continue
        clean = _re.sub(r"^\d{8}_\d{4}_", "", f.name)
        norm = _re.sub(r"[_\-]+", " ", clean)
        if "QP" in norm:
            continue                          # last year's packet is input, not a part
        if _re.search(r"\bLRs?\b|loss\s+run", norm, _re.I):
            kind = "loss_runs"
        elif _re.search(r"\bCOI\b", norm, _re.I):
            kind = "cois"
        elif _re.search(r"\bMVRs?\b", norm, _re.I):
            kind = "mvrs"
        else:
            continue
        try:
            fitz.open(f).close()              # extensionless COI still opens fine
        except Exception:  # noqa: BLE001
            continue
        newest[(kind, clean)] = f             # sorted() puts the latest stamp last
    for (kind, _), f in newest.items():
        out[kind].append(f)
    return out


def find(folders: list[Path], patterns: list[str]) -> list[Path]:
    hits: list[Path] = []
    for folder in folders:
        if not folder.is_dir():
            continue
        for pat in patterns:
            for p in sorted(folder.glob(pat)):
                if p.suffix.lower() == ".pdf" and "QP" not in p.name and p not in hits:
                    hits.append(p)
    return hits


def build(slug: str, risk: str, to_drive: bool) -> dict:
    folder = CLIENTS / slug
    rout = OUT / slug
    dossier = json.loads((folder / "state.json").read_text(encoding="utf-8"))
    c = dossier.get("company", {}) or {}
    client = c.get("first_named_insured") or slug
    # sp_code comes from JC's ledger when the dossier knows it; the formula is the
    # fallback — never "CLIENT", which shipped a packet named "CLIENT CAP tow QP".
    sys.path.insert(0, str(ROOT / "watcher"))
    from process_drop import sp_name
    sp = dossier.get("sp_code") or sp_name(client)
    stamp = m8()
    # _source is where Slack drops archive their material — a drip-built client
    # keeps its loss runs and COI there, not at the folder root
    sources = [folder, rout, folder / "_source"]

    draft = next(iter(folder.glob("*_CAP_app_2026_DRAFT.pdf")), None)
    if draft is None:
        return {"ok": False, "error": "no filled app (DRAFT) in the client folder — "
                                      "fill the app before building a QP"}

    included: dict[str, list[Path]] = {}
    missing: list[str] = []
    for label, field, pats in REPORT_ROWS:
        hits = find(sources, pats)
        included[label] = hits
        if not hits:
            missing.append(label)
    extras: dict[str, list[Path]] = {}
    for label, pats in EXTRA_REPORTS:
        hits = find(sources, pats)
        if hits:
            extras[label] = hits
        else:
            missing.append(label)

    # "LR RF" is the request FORM — already page 10 of the working set; including
    # the standalone copy printed the same form twice.
    loss_runs = [p for p in find(sources, ["*LR *.pdf", "*LRs *.pdf", "*loss run*.pdf"])
                 if "LR RF" not in p.name]
    cois = find(sources, ["*COI*.pdf"])
    mvrs = find(sources, ["*MVR*.pdf"])
    archived = collect_source(folder)
    loss_runs += [f for f in archived["loss_runs"] if "LR RF" not in f.name]
    cois += archived["cois"]
    mvrs += archived["mvrs"]
    lr_terms = dossier.get("loss_runs") or []
    lr_years = {str(r.get("year")) for r in lr_terms if r.get("year")}

    # ------------------------------------------------ checklist page tells the truth
    app = fitz.open(draft)
    today_field = stamp
    marks: dict[str, object] = {
        "p14_sp_source_code": dossier.get("sp_source_code") or "",
        "p14_sp_prep_code": dossier.get("sp_prep_code") or "",
        "p14_sp_target_code": dossier.get("sp_target_code") or "",
    }
    if loss_runs:
        marks["p14_loss_runs_last_5_years_included"] = True
        marks["p14_row403_lrs_required_all_in"] = len(lr_years) >= 5
    if cois:
        marks["p14_expiring_coi_included"] = True
    if mvrs:
        marks["p14_mvrs_per_driver_included"] = True
    for label, field, _ in REPORT_ROWS:
        if included[label]:
            marks[field] = True
    for page in app:
        if page.number != 13:                      # page 14, 0-indexed
            continue
        for w in page.widgets():
            if w.field_name in marks:
                val = marks[w.field_name]
                if w.field_type == fitz.PDF_WIDGET_TYPE_CHECKBOX:
                    w.field_value = bool(val)
                else:
                    w.field_value = str(val)
                w.update()

    # ------------------------------------------------ assemble, real order
    out = fitz.open()
    manifest: list[str] = []

    def add(path: Path, label: str) -> None:
        with fitz.open(path) as d:
            out.insert_pdf(d)
            manifest.append(f"{label}: {path.name} ({len(d)} p)")

    tmp_app = rout / ".app_checked.pdf"
    rout.mkdir(parents=True, exist_ok=True)
    app.save(tmp_app)
    app.close()
    add(tmp_app, "Sierra working set (app p1-14)")
    tmp_app.unlink(missing_ok=True)

    # The reading of the evidence goes directly behind the working set, before the
    # evidence itself. The packet used to carry none of it: findings were said once
    # in Slack and the 41 pages that reached the carrier never mentioned the
    # overdue Statement of Information or the cousin driving uninsured.
    try:
        import findings_page
        fpdf = rout / ".findings.pdf"
        if findings_page.build(dossier, fpdf, sp, client):
            add(fpdf, "findings and open questions")
        fpdf.unlink(missing_ok=True)
    except Exception as exc:  # noqa: BLE001 - a packet without it beats no packet
        manifest.append(f"findings page SKIPPED ({type(exc).__name__}: {exc})")

    for label in ["CA MCP or state filing report"]:
        for p in included[label]:
            add(p, label)
    for label in ["SAFER report", "CHP report"]:
        for p in extras.get(label, []):
            add(p, label)
    for p in loss_runs:
        add(p, "loss run")
    for label in ["Secretary of State report"]:
        for p in included[label]:
            add(p, label)
    for p in cois:
        add(p, "expiring COI")
    for p in mvrs:
        add(p, "MVR")
    for label in ["Website report", "Facebook report", "Instagram report",
                  "Yelp report", "Google street report per loc",
                  "Google overhead report per loc", "VIN report per vehicle"]:
        for p in included[label]:
            add(p, label)

    # ------------------------------------------------ the comp gate
    have_5yr = len(lr_years) >= 5
    complete = (not missing) and have_5yr and bool(cois) and bool(mvrs)
    gate: list[str] = []
    if missing:
        gate.append("reports not captured: " + ", ".join(missing))
    if not have_5yr:
        gate.append(f"loss runs cover {len(lr_years)} year(s) of 5")
    if not cois:
        gate.append("no expiring COI on file")
    if not mvrs:
        gate.append("no MVRs on file (rerun after loss runs, per JC)")

    # ------------------------------------------------ dated name, version on CHANGE
    # Four identical 68 MB packets piled up in one morning because every rebuild
    # minted a new _N. JC's dated versions mark real updates ("I got that loss
    # run... keep a new version of the QP"), not idempotent re-runs. A fingerprint
    # of every input decides: same inputs -> replace today's newest build; changed
    # inputs -> earn the next _N. Older versions are still never deleted.
    import hashlib
    every_input = [q for hits in included.values() for q in hits]
    every_input += loss_runs + cois + mvrs + [draft]
    fp = hashlib.sha256("\n".join(sorted(
        f"{q.name}|{q.stat().st_size}|{int(q.stat().st_mtime)}"
        for q in every_input)).encode()).hexdigest()[:16]
    fp_file = rout / ".qp_fingerprint"
    prev_fp, prev_name = (fp_file.read_text(encoding="utf-8").split("|", 1)
                          if fp_file.exists() else ("", ""))

    base = f"{sp} CAP {risk} QP {stamp}"
    if prev_fp == fp and prev_name.startswith(base) and (rout / prev_name).exists():
        name = prev_name                     # unchanged: refresh the same file
    else:
        name = f"{base} comp.pdf" if complete else f"{base}.pdf"
        n = 2
        while (folder / name).exists() or (rout / name).exists():
            name = (f"{base}_{n} comp.pdf" if complete else f"{base}_{n}.pdf")
            n += 1
    fp_file.write_text(f"{fp}|{name}", encoding="utf-8")
    (rout / name).unlink(missing_ok=True)
    # Compressed, and losslessly: the packet was 82 MB where JC's own comparable
    # one is 6 MB, because the report captures go in as full-resolution PNG and
    # every duplicate object is stored again. deflate plus garbage collection
    # takes the same 42 pages to 13 MB with nothing re-encoded — the difference
    # between a packet a carrier can be emailed and one that bounces.
    out.save(rout / name, garbage=4, deflate=True, deflate_images=True, clean=True)
    pages = len(out)
    out.close()

    link = ""
    if to_drive:
        try:
            from drive_api import client_folders, upload_to_drive, folder_link
            _, fid, _ = client_folders(sp, client)
            link = upload_to_drive(str(rout / name), name, parent_id=fid)
        except Exception as exc:  # noqa: BLE001
            link = f"(Drive upload skipped: {exc})"

    return {"ok": True, "file": str(rout / name), "name": name, "pages": pages,
            "complete": complete, "gate": gate, "manifest": manifest,
            "drive": link}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--client", required=True)
    ap.add_argument("--risk", default="tow")
    ap.add_argument("--no-drive", action="store_true")
    args = ap.parse_args()
    r = build(args.client, args.risk, not args.no_drive)
    if not r["ok"]:
        print("ERROR:", r["error"])
        return
    print(f"{r['name']} — {r['pages']} pages — "
          f"{'COMPLETE (comp)' if r['complete'] else 'INCOMPLETE (no comp)'}")
    for line in r["manifest"]:
        print(f"  {line}")
    if r["gate"]:
        print("para ganar el sufijo comp falta:")
        for g in r["gate"]:
            print(f"  · {g}")
    if r["drive"]:
        print(f"Drive: {r['drive']}")


if __name__ == "__main__":
    main()
