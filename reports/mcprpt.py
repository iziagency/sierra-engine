r"""CA DMV Motor Carrier Permit report — the state's own PDF, kept as-is.

The DMV publishes the Active MCP list as a Tableau view whose `.pdf` export is
exactly the page JC prints for the packet (page 15 of the real Lakeside QP is this
export, untouched). So the export IS the report — no letterhead, no rebuild —
and the same bytes get parsed for the compare-and-question pass.

Two details that cost an hour to find, written down so they stay found:
  * the filter field is `CA number` — lowercase n
  * CA numbers are zero-padded to 7 characters (564061 -> 0564061)

Not geo-blocked (unlike FMCSA) — runs from anywhere.

Emits:
  <SP> CAP MCP report <M.D.YY>.pdf    the DMV's own export
  mcp_report.json                     parsed record + questions
"""
from __future__ import annotations

import argparse
import datetime
import json
import re
import sys
import urllib.request
from pathlib import Path

import fitz

ROOT = Path(__file__).resolve().parent.parent
CLIENTS = ROOT / "app-form" / "clients"
VIEW = ("https://analytics.dmv.ca.gov/t/TableauProduction01/views/"
        "ActiveMCPPermitSearch/MCPPermitSearch2.pdf"
        "?CA%20number={ca}&:showVizHome=no")
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")


def pad_ca(ca: str) -> str:
    return re.sub(r"\D", "", str(ca or "")).zfill(7)


def fetch(ca: str, dest: Path) -> str:
    url = VIEW.format(ca=pad_ca(ca))
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=90) as r:
            data = r.read()
    except Exception as exc:  # noqa: BLE001
        return f"DMV fetch failed: {exc}"
    if not data.startswith(b"%PDF"):
        return "DMV did not return a PDF"
    dest.write_bytes(data)
    return ""


def parse(pdf: Path) -> dict:
    with fitz.open(pdf) as d:
        text = "\n".join(p.get_text() for p in d)
    rec: dict = {"source": "analytics.dmv.ca.gov (CA DMV Active MCP list)",
                 "fields": {}, "found": False}
    m = re.search(r"\b(\d{7})\s+([A-Z0-9 &.,'\-]+?)\s+Phone:\s*([()\d\s\-]+)", text)
    if m:
        rec["found"] = True
        rec["fields"]["ca_number"] = m.group(1)
        rec["fields"]["legal_name"] = m.group(2).strip()
        rec["fields"]["phone"] = re.sub(r"\s+", " ", m.group(3)).strip()
    m = re.search(r"DBA\s*-\s*([^\n]*)", text)
    if m and m.group(1).strip() not in ("", "NA"):
        rec["fields"]["dba"] = m.group(1).strip()
    m = re.search(r"Liability Insurance\s*-\s*(.+?)\s*\(", text, re.DOTALL)
    if m:
        rec["fields"]["liability_carrier"] = re.sub(r"\s+", " ", m.group(1)).strip()
    m = re.search(r"Workers Comp Insurance\s*-\s*(.+?)\s*\(", text, re.DOTALL)
    if m:
        rec["fields"]["wc_carrier"] = re.sub(r"\s+", " ", m.group(1)).strip()
    return rec


def _norm(s: str) -> str:
    s = re.sub(r"[^a-z0-9 ]", " ", str(s or "").lower())
    drop = {"insurance", "company", "co", "inc", "usa", "specialty", "group",
            "llc", "the", "ins", "casualty", "mutual", "program"}
    return " ".join(w for w in s.split() if w not in drop)


def compare(dossier: dict, rec: dict) -> list[str]:
    qs: list[str] = []
    c = dossier.get("company", {}) or {}
    f = rec.get("fields", {})
    if not rec.get("found"):
        qs.append(f"No active Motor Carrier Permit found for CA # "
                  f"{c.get('state_filing_number') or '(none on app)'} — an inactive "
                  f"MCP stops a CA tow submission; confirm the CA number and permit "
                  f"status with the insured.")
        return qs
    app_name, dmv_name = c.get("first_named_insured", ""), f.get("legal_name", "")
    if app_name and dmv_name and _norm(app_name) != _norm(dmv_name):
        qs.append(f"Legal name: app says “{app_name}”, the CA MCP list has "
                  f"“{dmv_name}” — confirm the named insured.")
    app_carrier = c.get("current_auto_carrier", "")
    dmv_carrier = f.get("liability_carrier", "")
    if app_carrier and dmv_carrier and _norm(app_carrier) != _norm(dmv_carrier):
        qs.append(f"Current carrier: app says “{app_carrier}”, the state's active "
                  f"MCP record shows “{dmv_carrier}” — which is in force today? A "
                  f"stale filing here is a classic underwriter question.")
    app_ph = re.sub(r"\D", "", str(c.get("office_phone") or c.get("contact_cell") or ""))
    dmv_ph = re.sub(r"\D", "", f.get("phone", ""))
    if app_ph and dmv_ph and app_ph[-10:] != dmv_ph[-10:]:
        qs.append(f"Phone on the MCP record ({f.get('phone')}) differs from the app "
                  f"({c.get('office_phone') or c.get('contact_cell')}).")
    return qs


def run(slug: str, ca: str | None = None) -> dict:
    folder = CLIENTS / slug
    dossier = json.loads((folder / "state.json").read_text(encoding="utf-8"))
    sp = dossier.get("sp_code") or "CLIENT"
    ca = ca or (dossier.get("company", {}) or {}).get("state_filing_number") or ""
    if not re.sub(r"\D", "", str(ca)):
        return {"ok": False, "error": "no CA number on the app"}

    today = datetime.date.today()
    stamp = f"{today.month}.{today.day}.{str(today.year)[2:]}"
    out = ROOT / "reports" / "out" / slug
    out.mkdir(parents=True, exist_ok=True)
    pdf = out / f"{sp} CAP MCP report {stamp}.pdf"

    err = fetch(ca, pdf)
    if err:
        return {"ok": False, "error": err}
    rec = parse(pdf)
    questions = compare(dossier, rec)
    (out / "mcp_report.json").write_text(json.dumps(
        {"retrieved": datetime.datetime.now().isoformat(timespec="seconds"),
         **rec, "questions": questions}, indent=1), encoding="utf-8")
    return {"ok": True, "pdf": str(pdf), "record": rec, "questions": questions}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--client", required=True)
    ap.add_argument("--ca")
    args = ap.parse_args()
    r = run(args.client, args.ca)
    if not r["ok"]:
        print("ERROR:", r["error"])
        return
    print(f"MCP — {r['pdf']}")
    for k, v in r["record"]["fields"].items():
        print(f"  {k}: {v}")
    for q in r["questions"]:
        print(f"  · {q}")


if __name__ == "__main__":
    main()
