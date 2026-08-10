r"""Loss runs -> the app. JC's gospel rule, implemented.

From the 7.22 call, verbatim: "if we have a different number... overwrite that and
use this number... the loss history is the gospel" and "it annoys me when my team
doesn't update that — it's a 2025 carrier, it's right there, that's the right
answer. You got the loss runs too, why isn't it in the current carrier?"

So a loss run outranks the application: effective dates and policy numbers of an
expired term do not change, which is precisely why underwriters trust them. This
module extracts each loss run, writes its facts into the dossier with a notation
(gospel overwrites, but never silently), promotes the newest term's carrier and
expiration into `current_auto_carrier` / `current_auto_expires`, and runs the
60-day clock: a loss run valued more than 60 days before the expiration date
fails most carriers' submission requirements, and three days late is a rejected
file ("you have a 63-day-old loss run? Too bad. Quote, your file's rejected").

Formats are per-carrier chaos — MSIG is a text grid, Obsidian two different
layouts, NICO a scan with no text layer at all — so extraction is a model pass.
But the gospel fields (policy number, dates) are verified against the PDF's own
text layer wherever one exists: a model-read policy number that does not appear
verbatim in the text is flagged, not trusted.

Usage:
  python lossruns.py --client lakeside-towing-llc --files "a.pdf" "b.pdf"
  (also called by process_drop when a drop's PDFs look like loss runs)
"""
from __future__ import annotations

import argparse
import datetime
import json
import re
import sys
from pathlib import Path

import fitz

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
CLIENTS = ROOT / "app-form" / "clients"
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "shared"))
import formatting  # noqa: E402 - needs sys.path set up first

LR_PROMPT = """You are the loss-run reader of the Sierra Pacific cap-app skill.

Read the attached loss run PDF(s) and return ONE json object:

{{"loss_runs": [
   {{"carrier": "insurer legal/program name as printed",
     "policy_number": "...",
     "effective_date": "M/D/YYYY",
     "expiration_date": "M/D/YYYY",
     "valuation_date": "M/D/YYYY",
     "claim_count": 0,
     "total_incurred": 0.0,
     "claims": [{{"date_of_loss": "M/D/YYYY", "type": "...", "status": "...",
                  "total_incurred": 0.0, "driver_name": "as printed, or null",
                  "description": "one short line"}}]
   }}
]}}

Rules:
- One entry per policy TERM. A single PDF can carry two terms (e.g. NICO 2021
  and 2022) — split them into two entries.
- valuation_date is the "as of" / "created" / "run" date printed on the report.
- Copy policy numbers character by character; if any character is unclear, null.
- "No Claims Reported" means claim_count 0 and an empty claims list.
- NEVER invent. Missing or illegible -> null.

Output ONLY the json object, no prose, no fences.
"""

# Words that mark a PDF as a loss run rather than an app, COI or report.
LR_MARKERS = re.compile(
    r"loss\s*run|claim\s*grid|claims\s*cost|loss\s*history|claims\s*where|"
    r"no\s*claims\s*reported|total\s*incurred", re.I)


def looks_like_lossrun(pdf: Path) -> bool:
    try:
        with fitz.open(pdf) as d:
            text = d[0].get_text()
            if LR_MARKERS.search(text):
                return True
            # A scanned loss run has no text, so the filename is the only signal —
            # and Slack sanitizes spaces into underscores, which count as word
            # characters: \b never fires inside "NICO_LRs_2021", and the NICO scan
            # sailed past this classifier twice. Treat _ and - as separators.
            name = re.sub(r"[_\-]+", " ", pdf.name)
            return bool(re.search(r"\bLRs?\b", name, re.I))
    except Exception:  # noqa: BLE001
        return False


def _pdf_text(pdf: Path) -> str:
    try:
        with fitz.open(pdf) as d:
            return "\n".join(p.get_text() for p in d)
    except Exception:  # noqa: BLE001
        return ""


def _norm(s) -> str:
    return re.sub(r"[^0-9A-Za-z]", "", str(s or "")).upper()


def _date(v) -> datetime.date | None:
    m = re.match(r"\s*(\d{1,2})[./-](\d{1,2})[./-](\d{2,4})", str(v or ""))
    if not m:
        return None
    mo, da, yr = (int(x) for x in m.groups())
    if yr < 100:
        yr += 2000
    try:
        return datetime.date(yr, mo, da)
    except ValueError:
        return None


def m8(d: datetime.date | None) -> str | None:
    """Thin wrapper: formatting itself now lives in shared/formatting.py,
    which also accepts a bare date object directly (JC's 7.27 consolidation
    of the four m8/m8_date copies into one module)."""
    return formatting.format_date(d)


def extract(files: list[str]) -> tuple[list[dict], list[str]]:
    """Model-read the loss runs, then verify gospel fields against the text layer."""
    from process_drop import claude_run, extract_json, extract_budget

    prompt = LR_PROMPT + "\nATTACHED FILES (read each):\n" + "\n".join(files)
    data = extract_json(claude_run(prompt, extract_budget(len(files))))
    runs = [r for r in (data.get("loss_runs") or []) if isinstance(r, dict)]

    notes: list[str] = []
    corpus = _norm(" ".join(_pdf_text(Path(f)) for f in files))
    for r in runs:
        pn = r.get("policy_number")
        # Verify the policy number against the PDFs' own text. A scanned loss run
        # has no text layer to check against — say so instead of pretending.
        if pn and corpus:
            if _norm(pn) not in corpus:
                notes.append(f"policy number “{pn}” was read by the model but does "
                             f"not appear in any PDF's text layer — verify by hand "
                             f"before it goes on the app")
                r["_unverified"] = True
        elif pn and not corpus:
            notes.append(f"policy number “{pn}” comes from a scanned image (no text "
                         f"layer to verify against) — double-check it")
            r["_unverified"] = True
    return runs, notes


def _dedupe(items: list[str]) -> list[str]:
    seen, out = set(), []
    for x in items:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out


def sixty_day_clock(runs: list[dict], expiration) -> list[str]:
    """The loss run clock. Valued > 60 days before expiration = rejected file."""
    notes = []
    exp = _date(expiration)
    for r in runs:
        vd = _date(r.get("valuation_date"))
        if not vd:
            notes.append(f"{r.get('carrier', '?')}: no valuation date found — the "
                         f"60-day rule cannot be checked")
            continue
        if exp:
            # These notes reach the broker's reply and the QP notes page, so
            # they obey JC's date shape (M.D.YY) like every other output.
            vd_s, exp_s = formatting.format_date(vd), formatting.format_date(exp)
            age_at_exp = (exp - vd).days
            if age_at_exp > 60:
                notes.append(f"{r.get('carrier', '?')} valued {vd_s} is "
                             f"{age_at_exp} days before expiration "
                             f"({exp_s}) — OUTSIDE the 60-day window, most "
                             f"carriers will reject; re-pull closer to submission")
            else:
                # "inside the 60-day window" about a term that already ended
                # reads as reassurance. The valuation may well be fine relative
                # to that term — and the term is still over.
                dead = ("; NOTE the term itself expired "
                        f"{(datetime.date.today() - exp).days} days ago, so a "
                        f"fresh loss run on the CURRENT policy is what a "
                        f"carrier will want"
                        if exp < datetime.date.today() else "")
                notes.append(f"{r.get('carrier', '?')} valued {vd_s} — "
                             f"{age_at_exp} days before expiration, inside the "
                             f"60-day window{dead}")
    return notes


def _normalised_claim(c: dict) -> dict:
    """A claim dict with its date_of_loss in JC's M.D.YY — unchanged if it is
    not a recognisable date, and never dropped. The model is asked for
    date_of_loss as M/D/YYYY (see LR_PROMPT above) and that raw spelling used
    to ride straight into state.json — rts_fill.py was the only place that
    ever reformatted it, and only at write time. This makes ingest match
    write: one format, chosen once, at the point the gospel data is written.
    """
    if not isinstance(c, dict) or not c.get("date_of_loss"):
        return c
    return {**c, "date_of_loss": formatting.format_date(c["date_of_loss"])
                                  or c["date_of_loss"]}


def apply_gospel(dossier: dict, runs: list[dict]) -> tuple[list[dict], list[str]]:
    """Write loss run facts into the dossier. Gospel overwrites, never silently."""
    changes: list[str] = []
    lrs = dossier.setdefault("loss_runs", [])
    if not isinstance(lrs, list):
        dossier["loss_runs"] = lrs = []

    def year_of(r):
        d = _date(r.get("effective_date"))
        return str(d.year) if d else None

    by_year = {}
    for row in lrs:
        if isinstance(row, dict) and row.get("year"):
            by_year[str(row["year"])] = row

    for r in sorted(runs, key=lambda x: _date(x.get("effective_date"))
                    or datetime.date.min):
        yr = year_of(r)
        if not yr:
            continue
        eff, exp = _date(r.get("effective_date")), _date(r.get("expiration_date"))
        entry = by_year.get(yr)
        if entry is None:
            entry = {"year": yr}
            lrs.append(entry)
            by_year[yr] = entry
        # claim details ride along: the RTS driver rows and the UW Qs page need
        # the date, type and amount of each claim, not just the count
        if r.get("claims"):
            entry["claims"] = [_normalised_claim(c) for c in r["claims"]]
            from process_drop import _dedupe_claims
            _dedupe_claims(entry)
        for src, dst in (("carrier", "carrier"), ("policy_number", "policy_number"),
                         ("claim_count", "claim_count"),
                         ("total_incurred", "total_incurred"),
                         ("valuation_date", "valuation_date")):
            new = r.get(src)
            if new in (None, ""):
                continue
            old = entry.get(dst)
            if old not in (None, "") and _norm(old) != _norm(new):
                if dst == "carrier":
                    # Names are NOT gospel: the same reader returned "Obsidian
                    # Specialty Insurance Company" one run and "Obsidian Specialty
                    # (TARP/TUMI program)" the next, off the same PDF. Printed
                    # facts (numbers, dates) overwrite; names keep the first
                    # reading and a mismatch worth acting on shows up in the
                    # MCP comparison instead.
                    continue
                # gospel: the loss run wins, with a notation — JC: "overwrite that
                # and use this number... maybe make a little notation"
                changes.append(f"loss_runs[{yr}].{dst}: “{old}” → “{new}” "
                               f"(corrected per loss run — loss history is gospel)")
            elif old in (None, ""):
                changes.append(f"loss_runs[{yr}].{dst} = “{new}” (from loss run)")
            entry[dst] = new
        if eff and exp:
            entry["effective_dates"] = f"{m8(eff)} - {m8(exp)}"
        if r.get("_unverified"):
            entry["unverified"] = True

    # Promote the newest term into current carrier / expiration — the exact update
    # JC complained his team skips.
    dated = [r for r in runs if _date(r.get("expiration_date"))]
    if dated:
        newest = max(dated, key=lambda r: _date(r.get("expiration_date")))
        exp = _date(newest.get("expiration_date"))
        today = datetime.date.today()
        c = dossier.setdefault("company", {})
        if exp and exp >= today:
            # Dates are gospel: an expiration printed on a loss run is a fact.
            old = c.get("current_auto_expires")
            if _norm(old) != _norm(m8(exp)):
                changes.append(f"company.current_auto_expires: “{old or '(blank)'}”"
                               f" → “{m8(exp)}” (per newest loss run — gospel)")
                c["current_auto_expires"] = m8(exp)
            # The carrier NAME is not: a loss run's letterhead often shows the
            # program or wholesaler, not the insurer. Lakeside's 2025 grid is
            # titled "Amwins Trucking TUMI Program" while the paper is MSIG
            # Specialty — overwriting MSIG with the program name would have put
            # a wrong carrier on the app with full confidence. Blank fills;
            # a mismatch becomes a question, never a rewrite.
            lr_carrier = newest.get("carrier")
            if lr_carrier:
                cur = c.get("current_auto_carrier")
                if not cur:
                    c["current_auto_carrier"] = lr_carrier
                    changes.append(f"company.current_auto_carrier = "
                                   f"“{lr_carrier}” (from newest loss run)")
                elif _norm(cur) != _norm(lr_carrier):
                    changes.append(f"QUESTION: newest loss run is issued under "
                                   f"“{lr_carrier}” but the app says the current "
                                   f"carrier is “{cur}” — a loss run header often "
                                   f"names the program/wholesaler rather than the "
                                   f"insurer; confirm which name belongs on the app")
        else:
            # The newest term anyone can see has already ended. Declining to
            # promote a dead date is right, but staying quiet about it is not:
            # SUMMI1's app then went out carrying an even OLDER expiration and
            # nothing said the risk looks uninsured. On a renewal book this is
            # the most important sentence in the file.
            days = (today - exp).days
            changes.append(
                f"QUESTION: the newest policy term on file "
                f"({newest.get('carrier') or 'carrier'} per loss run) "
                f"EXPIRED {m8(exp)} — {days} days ago. The app "
                f"still shows “{c.get('current_auto_expires') or '(blank)'}”. "
                f"What coverage is in force today, and is there a newer term "
                f"nobody has sent us?")
    return runs, changes


def run(slug: str, files: list[str]) -> dict:
    folder = CLIENTS / slug
    dossier = json.loads((folder / "state.json").read_text(encoding="utf-8"))

    runs, verify_notes = extract(files)
    runs, changes = apply_gospel(dossier, runs)
    # two terms often share one PDF and one valuation date; say each thing once
    clock = _dedupe(sixty_day_clock(
        runs, (dossier.get("company") or {}).get("policy_effective_date")
        or (dossier.get("company") or {}).get("current_auto_expires")))

    (folder / "state.json").write_text(json.dumps(dossier, indent=1),
                                       encoding="utf-8")
    return {"ok": True, "slug": slug, "terms": len(runs), "changes": changes,
            "notes": verify_notes + clock,
            "years": sorted({str((_date(r.get('effective_date')) or datetime.date.min).year)
                             for r in runs if _date(r.get("effective_date"))})}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--client", required=True)
    ap.add_argument("--files", nargs="+", required=True)
    args = ap.parse_args()
    r = run(args.client, args.files)
    print(f"loss runs — {r['slug']}: {r['terms']} term(s), years {', '.join(r['years'])}")
    print("cambios aplicados (gospel):")
    for c in r["changes"]:
        print(f"  · {c}")
    print("notas:")
    for n in r["notes"]:
        print(f"  · {n}")


if __name__ == "__main__":
    main()
