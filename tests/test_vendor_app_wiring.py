"""The drop pipeline makes both phase-1 documents, not just the app.

"if we can do two things: fill out an app, and have it populate any vendor
apps… start with the SP cap app and the RTS app every time."

`build_vendor_apps` is the seam: it applies the routing rule and, when the risk
qualifies, produces the RTS without anyone asking. When it does not qualify it
still says why, because the broker reading the Slack reply needs to know
whether a missing RTS was a decision or an omission.
"""
from __future__ import annotations

import process_drop as pd

CA_TOW = {
    "sp_code": "TESTT1",
    "company": {"location_address": "1 Main St, San Bernardino, CA 92404"},
    "operations": {"tow_disabled_autos": 100},
    "vehicle_totals": {"power_units": 2},
}


def test_a_qualifying_risk_gets_the_rts_built(monkeypatch):
    calls = []
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(pd.__file__).resolve().parent.parent / "reports"))
    import rts_fill
    monkeypatch.setattr(rts_fill, "fill",
                        lambda slug: calls.append(slug) or
                        {"ok": True, "file": r"C:\tmp\SP CAP RTS supp app 8.1.26.xlsx",
                         "cells": 54})

    out = pd.build_vendor_apps(CA_TOW, "test-towing-llc")["rts"]
    assert calls == ["test-towing-llc"]
    assert out["applies"] is True
    assert out["cells"] == 54


def test_an_out_of_state_risk_builds_nothing_and_says_so(monkeypatch):
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(pd.__file__).resolve().parent.parent / "reports"))
    import rts_fill

    def boom(slug):
        raise AssertionError("must not build an RTS for a Texas risk")
    monkeypatch.setattr(rts_fill, "fill", boom)

    dossier = dict(CA_TOW, company={"location_address": "1 Main St, Dallas, TX 75201"})
    out = pd.build_vendor_apps(dossier, "test-towing-llc")["rts"]
    assert out["applies"] is False
    assert out["file"] == ""
    assert "California" in out["reason"]


def test_a_missing_state_is_flagged_as_a_gap_not_a_decision():
    dossier = dict(CA_TOW, company={})
    out = pd.build_vendor_apps(dossier, "test-towing-llc")["rts"]
    assert out["unknown"] is True


def test_an_rts_failure_never_costs_us_the_cap_app(monkeypatch):
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(pd.__file__).resolve().parent.parent / "reports"))
    import rts_fill

    def boom(slug):
        raise RuntimeError("template missing")
    monkeypatch.setattr(rts_fill, "fill", boom)

    out = pd.build_vendor_apps(CA_TOW, "test-towing-llc")["rts"]
    assert out["applies"] is True
    assert "template missing" in out["error"]
