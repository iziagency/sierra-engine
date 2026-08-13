"""SAFER report — the FMCSA Company Snapshot, parsed and cross-checked.

Pages 16-18 of the real Lakeside QP are this: safer.fmcsa.dot.gov's Company
Snapshot for the carrier's USDOT number, captured full-page. The capture reuses
webrpt's proven path and needs Playwright + a US IP, so it cannot run in this
suite. What is tested here is the logic that is new and does not touch the
network: the URL built from the USDOT, the field parse, and the questions the
parse raises against the app.

The fixture is transcribed from the real captured page, not invented — every
label and value below appears on page 16-17 of that QP.
"""
from __future__ import annotations

import saferrpt


# Straight off the real Company Snapshot for SANTOYO/USDOT 3416380, the labels
# and values as SAFER lays them out (a browser's inner_text of that page).
FIXTURE = """
Company Snapshot SANTOYO TOWING LLC USDOT Number: 3416380
USDOT Status: ACTIVE
Out of Service Date: None
MCS-150 Form Date: 05/03/2026
MCS-150 Mileage (Year): 100,000 (2026)
Operating Authority Status: NOT AUTHORIZED
Legal Name: SANTOYO TOWING LLC
DBA Name:
Physical Address: 7050 PERRIS HILL RD SAN BERNRDNO, CA 92404
Phone: (909) 685-9794
Power Units: 1
Drivers: 1
Carrier Operation: Intrastate Only (Non-HM)
"""


def test_the_url_is_the_company_snapshot_for_the_usdot():
    url = saferrpt.snapshot_url("3416380")
    assert "safer.fmcsa.dot.gov" in url
    assert "queryCarrierSnapshot" in url
    assert "3416380" in url


def test_the_usdot_is_cleaned_of_any_stray_punctuation():
    assert saferrpt.snapshot_url("USDOT 3416380") == saferrpt.snapshot_url("3416380")


def test_the_parse_reads_the_fields_the_underwriter_reads():
    f = saferrpt.parse(FIXTURE)
    assert f["usdot_status"] == "ACTIVE"
    assert f["legal_name"] == "SANTOYO TOWING LLC"
    assert f["power_units"] == 1
    assert f["drivers"] == 1
    assert f["out_of_service"] is False
    assert "05/03/2026" in f["mcs150_date"]


def test_a_fleet_count_mismatch_becomes_a_question():
    # App claims three trucks, SAFER's authoritative power-unit count is one.
    dossier = {"company": {"first_named_insured": "SANTOYO TOWING LLC",
                           "total_vehicles": 3, "usdot_number": "3416380"}}
    qs = saferrpt.compare(dossier, saferrpt.parse(FIXTURE))
    assert any("power unit" in q.lower() for q in qs), qs


def test_a_matching_fleet_count_raises_no_power_unit_question():
    dossier = {"company": {"first_named_insured": "SANTOYO TOWING LLC",
                           "total_vehicles": 1, "usdot_number": "3416380"}}
    qs = saferrpt.compare(dossier, saferrpt.parse(FIXTURE))
    assert not any("power unit" in q.lower() for q in qs)


def test_a_legal_name_mismatch_becomes_a_question():
    dossier = {"company": {"first_named_insured": "Different Towing Inc",
                           "usdot_number": "3416380"}}
    qs = saferrpt.compare(dossier, saferrpt.parse(FIXTURE))
    assert any("name" in q.lower() for q in qs), qs


def test_no_usdot_on_the_app_is_a_clean_refusal_not_a_crash(tmp_path, monkeypatch):
    monkeypatch.setattr(saferrpt, "CLIENTS", tmp_path)
    (tmp_path / "x").mkdir()
    (tmp_path / "x" / "state.json").write_text(
        '{"sp_code": "XX1", "company": {"first_named_insured": "X"}}',
        encoding="utf-8")
    r = saferrpt.run("x")
    assert r["ok"] is False
    assert "usdot" in r["error"].lower()


def test_the_runner_registers_safer_now_that_it_exists():
    import run_all as ra
    assert "safer" in ra.registered_names()
    # CHP is still absent — it never appeared in the reference QP and has no
    # capture to build a parser against.
    assert "chp" not in ra.registered_names()
