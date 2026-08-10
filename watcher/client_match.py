"""Identify which existing client a drop belongs to — no thread required.

At scale (20 people posting 20 apps a day into one channel) nobody replies in
the right thread and names arrive misspelled ("Brookfield towing LLL").
So matching runs on hard identifiers first, name similarity second:

  1. USDOT / FEIN / CA state filing number / MC  -> definitive
  2. Phone number (digits only)                  -> strong
  3. Normalized business name (suffixes stripped)-> strong
  4. Fuzzy name similarity >= 0.86               -> probable

The index is built from the engine's dossiers AND from the Clients/ folders in
the shared Drive, so a client that exists in Drive is found even if this
machine has never seen it.
"""
from __future__ import annotations

import json
import re
import sys
from difflib import SequenceMatcher
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

CLIENTS = Path(r"C:\dev\sierra-pacific\app-form\clients")

# legal suffixes and noise that must not decide identity
SUFFIXES = {
    "llc", "l l c", "lll", "inc", "inc.", "incorporated", "corp", "corporation",
    "co", "company", "ltd", "lp", "llp", "dba", "the",
}


def norm_name(name: str) -> str:
    if not name:
        return ""
    s = re.sub(r"[^a-z0-9 ]", " ", str(name).lower())
    words = [w for w in s.split() if w and w not in SUFFIXES]
    return " ".join(words)


def digits(value) -> str:
    return re.sub(r"\D", "", str(value or ""))


def identity(data: dict) -> dict:
    """Pull the identifying keys out of a dossier / fresh extraction."""
    c = (data or {}).get("company", {}) or {}
    return {
        "names": {n for n in (norm_name(c.get("first_named_insured")),
                              norm_name(c.get("dba"))) if n},
        "usdot": digits(c.get("usdot_number")),
        "fein": digits(c.get("fein")),
        "ca": digits(c.get("state_filing_number")),
        "mc": digits(c.get("mc_number")),
        "phones": {digits(p) for p in (c.get("contact_cell"), c.get("office_phone"))
                   if digits(p)},
    }


def build_index(drive_names: list[str] | None = None) -> list[dict]:
    """Every client the engine knows about, with its identity keys."""
    index = []
    for state in CLIENTS.glob("*/state.json"):
        try:
            data = json.loads(state.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        entry = identity(data)
        entry["slug"] = state.parent.name
        entry["label"] = (data.get("company", {}) or {}).get(
            "first_named_insured") or state.parent.name
        index.append(entry)

    # Drive folders — real convention "<SP> <DBA>" (space, no dash), legacy
    # sandbox convention "<SP> - <DBA>" (dash) also still parses — name-only
    # fallback for clients whose dossier isn't on this machine.
    from drive_api import parse_client_folder_name

    for folder in drive_names or []:
        _, dba = parse_client_folder_name(folder)
        if not dba:
            # real example: 'Prospects open/GUSTA1' has no business name at
            # all — skip rather than index a blank, matches-everything name.
            continue
        slug = re.sub(r"[^a-z0-9]+", "-", dba.lower()).strip("-")
        if any(e["slug"] == slug for e in index):
            continue
        index.append({"names": {norm_name(dba)}, "usdot": "", "fein": "", "ca": "",
                      "mc": "", "phones": set(), "slug": slug, "label": dba,
                      "drive_only": True})
    return index


def find_match(data: dict, index: list[dict]) -> tuple[str | None, str]:
    """Return (slug, human-readable reason) for the best match, or (None, '')."""
    new = identity(data)

    for key, label in (("usdot", "US DOT"), ("fein", "FEIN"),
                       ("ca", "CA filing #"), ("mc", "MC #")):
        if len(new[key]) >= 5:
            for e in index:
                if e[key] and e[key] == new[key]:
                    return e["slug"], f"{label} {new[key]} matches {e['label']}"

    for e in index:
        if new["phones"] & e["phones"]:
            return e["slug"], f"phone matches {e['label']}"

    for e in index:
        if new["names"] & e["names"]:
            return e["slug"], f"business name matches {e['label']}"

    best, best_score, best_label = None, 0.0, ""
    for e in index:
        for a in new["names"]:
            for b in e["names"]:
                score = SequenceMatcher(None, a, b).ratio()
                if score > best_score:
                    best, best_score, best_label = e["slug"], score, e["label"]
    if best and best_score >= 0.86:
        return best, f"name ~{int(best_score * 100)}% similar to {best_label}"
    return None, ""


def drive_client_folders(gateway=None) -> list[str]:
    """Names of every client folder in the real, read-only Clients drive
    (best effort) — flat AND legacy nested (most of the real ~15k Clients
    folders sit one level under a broker-initials container like "Clients
    AB"; a plain top-level listing missed almost all of them). Never touches
    the Claude sandbox — this is a survey of production, read-only.
    """
    try:
        from drive_api import CLIENTS_ROOT, GoogleDriveGateway, list_all_identity_folders, service
        gw = gateway or GoogleDriveGateway(service())
        return [m.name for m in list_all_identity_folders(gw, [CLIENTS_ROOT])]
    except Exception:  # noqa: BLE001 - matching still works on local dossiers
        return []
