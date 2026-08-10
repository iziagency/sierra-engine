"""The SP code has to be on the file BEFORE anything is named from it.

Shoreline came in as a brand-new client on 8.1 and its RTS workbook was
delivered to Drive as `CLIENT CAP RTS supp app 8.1.26.xlsx` — the literal
fallback string — next to a correctly named `SHORE1 CAP app 8.1.26.pdf`.

Cause: `build_vendor_apps` runs before the Drive block, and the Drive block was
where `data["sp_code"]` first got written. rts_fill re-reads state.json to name
its output, found no code on a first drop, and used its `or "CLIENT"` default.

The SP code is the client's identity — JC: "we always want to use the SP name
because it's our unique identifier" — so it belongs on the file at the moment
the dossier is first written, not as a side effect of uploading.
"""
from __future__ import annotations

import json

import process_drop as pd
import rts_fill


def test_rts_names_itself_from_the_code_on_a_brand_new_client(tmp_path, monkeypatch):
    clients = tmp_path / "clients"
    slug = "shoreline-towing-recovery-llc"
    (clients / slug).mkdir(parents=True)
    # A first drop as the pipeline writes it: dossier present, code already
    # anchored — this is the contract build_vendor_apps depends on.
    (clients / slug / "state.json").write_text(json.dumps({
        "sp_code": "SHORE1",
        "company": {"first_named_insured": "Shoreline Towing & Recovery LLC",
                    "location_address": "1420 Harbor Blvd, Oxnard, CA 93035"},
        "operations": {"tow_disabled_autos": 100},
        "vehicles": [{"year": 2021, "maker": "Ford", "vin": "1FDUF5HT8MDA31204",
                      "stated_value": 82000}],
        "drivers": [], "loss_runs": [], "coverages": {},
    }), encoding="utf-8")
    monkeypatch.setattr(rts_fill, "CLIENTS", clients)
    monkeypatch.setattr(rts_fill, "OUT", tmp_path / "out")

    r = rts_fill.fill(slug)
    assert "SHORE1 CAP RTS supp app" in r["file"]
    assert "CLIENT" not in r["file"]


def test_a_dossier_without_a_code_never_ships_as_CLIENT(tmp_path, monkeypatch):
    # The fallback itself is the bug: a file named CLIENT reaches an
    # underwriter meaning nothing. If the code is genuinely unknown, that is a
    # gap to report, not a filename to invent.
    clients = tmp_path / "clients"
    slug = "nameless-towing"
    (clients / slug).mkdir(parents=True)
    (clients / slug / "state.json").write_text(json.dumps({
        "company": {"first_named_insured": "Nameless Towing LLC"},
        "vehicles": [], "drivers": [], "loss_runs": [], "coverages": {},
    }), encoding="utf-8")
    monkeypatch.setattr(rts_fill, "CLIENTS", clients)
    monkeypatch.setattr(rts_fill, "OUT", tmp_path / "out")

    r = rts_fill.fill(slug)
    assert r["ok"] is False
    assert "SP code" in r["error"]


def test_build_vendor_apps_refuses_to_run_before_the_code_is_anchored(tmp_path, monkeypatch):
    # A guard at the seam: if the ordering ever regresses, this fails loudly
    # instead of shipping another CLIENT-named workbook.
    dossier = {
        "company": {"location_address": "1420 Harbor Blvd, Oxnard, CA 93035"},
        "operations": {"tow_disabled_autos": 100},
        "vehicle_totals": {"power_units": 2},
    }                                     # no sp_code
    out = pd.build_vendor_apps(dossier, "shoreline-towing-recovery-llc")["rts"]
    assert out["file"] == ""
    assert "SP code" in (out.get("error") or "")
