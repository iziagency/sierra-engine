"""Tests for reports/qp_read.py - the QP PDF -> dossier dict link.

JC on the 7.22 call: "the first thing we want to do is we want to be able to
take that QP and populate the RTS... maybe you just upload the QP directly
into the Slack channel, use this as the basis for all the information."
rts_fill.py could only read an already-built dossier (state.json); it had no
PDF capability. This is the missing link, verified against the real Lakeside
QP (LAKES CAP tow QP 7.27.26.pdf - 36 pages, 729 AcroForm widgets, 213 with
values, downloaded from the client's Drive on 2026-07-27).

`reports` is already on sys.path via tests/conftest.py, so this imports
qp_read the same way rts_fill.py does.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import qp_read
from qp_read import QPReadError, merge_into_dossier, read_qp

ROOT = Path(__file__).resolve().parent.parent
REAL_QP = ROOT / "reports" / "out" / "lakeside-towing-llc" / "LAKES CAP tow QP 7.27.26.pdf"

pytestmark = pytest.mark.skipif(
    not REAL_QP.exists(), reason="real Lakeside QP fixture not present in this checkout")


@pytest.fixture(scope="module")
def lakeside():
    return read_qp(str(REAL_QP))


# ---------------------------------------------------------------- identity block
# (the task's minimum bar: DBA, owner, FEIN, DOT, CA number, phone, email,
# vehicle/driver counts must all come back correctly)


def test_identity_block_names_and_entity_type(lakeside):
    c = lakeside["dossier"]["company"]
    assert c["dba"] == "Lakeside Towing LLC"
    assert c["first_named_insured"] == "Lakeside Towing LLC"
    assert c["owner_name"] == "Salomon Lakeside"
    assert c["entity_type"] == "llc"
    assert c["language"] == "english"


def test_identity_block_fein_preserved_verbatim_and_flagged(lakeside):
    # The real QP prints the FEIN with a DOT ("85.0604178"), not the dash
    # format used elsewhere in the pipeline ("85-0604178"). qp_read must not
    # silently "fix" this - preserve it, and flag it for a human to notice.
    c = lakeside["dossier"]["company"]
    assert c["fein"] == "85.0604178"
    assert any("85.0604178" in w for w in lakeside["warnings"])


def test_identity_block_ids_and_contact(lakeside):
    c = lakeside["dossier"]["company"]
    assert c["usdot_number"] == "3416380"
    assert c["state_filing_number"] == "564061"
    assert c["contact_cell"] == "909.685.9794"
    assert c["owner_email"] == "Sallakeside76@gmail.com"
    assert c["contact_title"] == "Owner"


def test_mc_number_na_placeholder_is_absent_not_literal(lakeside):
    c = lakeside["dossier"]["company"]
    # the QP prints "N/A" for MC number - never store that word as data
    assert c.get("mc_number") in (None,)
    for v in c.values():
        if isinstance(v, str):
            assert v.strip().upper() != "N/A"


def test_identity_block_vehicle_and_driver_counts(lakeside):
    c = lakeside["dossier"]["company"]
    assert c["total_vehicles"] == 1
    assert c["total_drivers"] == 1


def test_same_placeholder_resolved_never_literal(lakeside):
    c = lakeside["dossier"]["company"]
    # QP prints "Same" for these - must resolve to the referenced value.
    assert c["contact_name"] == c["owner_name"] == "Salomon Lakeside"
    assert c["contact_email"] == c["owner_email"] == "Sallakeside76@gmail.com"
    assert c["office_phone"] == c["contact_cell"] == "909.685.9794"
    assert c["location_address"] == c["mailing_address"]
    for v in c.values():
        if isinstance(v, str):
            assert v.strip().lower() != "same"


def test_boolean_flags(lakeside):
    c = lakeside["dossier"]["company"]
    assert c["cross_state_lines"] is False
    assert c["home_based"] is True
    assert c["new_venture"] is False
    assert c["dash_cameras"] is False
    # Both "SP No telematics" and "SP with telematics" are blank on the real
    # QP (verified directly) - genuinely unanswered on this document, so it
    # is correctly absent here even though state.json separately has `false`
    # for it (presumably from another source/pass) - see the final report.
    assert "telematics" not in c


def test_current_carrier_field_blank_on_this_qp_is_left_absent(lakeside):
    # Notable: the QP's own "SP Current carrier" text field is blank on this
    # document (even though state.json separately has MSIG from another
    # source) - never invent it from the loss-run carrier.
    c = lakeside["dossier"]["company"]
    assert "current_auto_carrier" not in c


def test_dash_cam_note_not_mistaken_for_a_brand(lakeside):
    # The real QP's "SP dash cam brand" field literally says "Willing to
    # install" while "No dash cam" is checked - that's a note, not a brand,
    # and dash_cameras_brand must not be set from it.
    c = lakeside["dossier"]["company"]
    assert "dash_cameras_brand" not in c


# ---------------------------------------------------------------- coverages


def test_coverages_shorthand_and_raw_amounts(lakeside):
    cov = lakeside["dossier"]["coverages"]
    assert cov["auto_liability"] == "1m"
    assert cov["on_hook"] == "150k"              # custom "other" amount, k-shorthand like the dossier convention
    assert cov["general_liability"] == "1m"
    assert cov["garage_keepers"] == "none"
    assert cov["building_property"] == "none"
    assert cov["business_personal_property"] == "none"
    assert cov["inland_marine"] == "none"
    assert cov["umbrella"] == "none"
    assert cov["total_stated_value"] == 50000    # raw number - this coverage has no k-tiers
    assert cov["workers_comp"] == {"status": "none"}


# ---------------------------------------------------------------- vehicles / drivers


def test_vehicles(lakeside):
    vehicles = lakeside["dossier"]["vehicles"]
    assert len(vehicles) == 1
    v = vehicles[0]
    assert v["year"] == 2016
    assert v["maker"] == "Hino"
    assert v["body_type"] == "CC"
    assert v["gvw"] == 25500
    assert v["max_towed"] == 2
    assert v["stated_value"] == 50000     # this QP's own printed figure, see the report for the $55k correction gap
    assert v["vin"] == "5PVNJ8JN5G4S52079"
    assert v["onhook"] == "150k"


def test_drivers(lakeside):
    drivers = lakeside["dossier"]["drivers"]
    assert len(drivers) == 1
    d = drivers[0]
    assert d["name"] == "Salomon Lakeside"
    assert d["state"] == "CA"
    assert d["license"] == "B5697020"
    assert d["birthday"] == "8.31.76"
    assert d["position"] == "Owner/Driver"
    assert d["years_experience"] == 6


def test_other_employees_count(lakeside):
    assert lakeside["dossier"]["other_employees_count"] == 0


# ---------------------------------------------------------------- loss runs (the positional-year bug)


def test_loss_run_years_derived_from_effective_dates_not_row_label(lakeside):
    """The QP's own field-name suffix for two blocks both say "2021", but the
    printed effective dates show one of them is really the 2022 policy year.
    fill_app.py's own loss_runs()/lossrun_page_values() document exactly this
    quirk for the OUTPUT template ("the real QP has '2025' written into the
    row printed '2024'"); reading the QP has the same trap. Trusting the
    field-name label would silently swap two years' carriers/policy numbers.
    """
    runs = {r["year"]: r for r in lakeside["dossier"]["loss_runs"]}
    assert set(runs) == {2025, 2024, 2023, 2022, 2021}
    assert runs[2025]["carrier"] == "MSIG Specialty Insurance USA, Inc"
    assert runs[2023]["policy_number"] == "TARP-CP-000000312-00"
    assert runs[2022]["policy_number"] == "73TRS123634"
    assert runs[2022]["carrier"] == "National Liability and Fire Insurance Company"
    assert runs[2021]["policy_number"] == "73TRS117469"


def test_loss_run_effective_dates_preserved_verbatim(lakeside):
    runs = {r["year"]: r for r in lakeside["dossier"]["loss_runs"]}
    assert runs[2025]["effective_dates"] == "8.3.25 - 8.3.26"


# ---------------------------------------------------------------- contracts / sections


def test_contracts(lakeside):
    names = {c["name"] for c in lakeside["dossier"]["contracts"]}
    assert names == {"Honk", "Allstate", "Agero"}


def test_revenue_sources(lakeside):
    rs = lakeside["dossier"]["revenue_sources"]
    assert rs["private_party_incoming_calls"] == 80
    assert rs["motor_clubs"] == 20
    assert rs["dealers_to_from_auctions"] is False
    assert rs["other"] is False


def test_operations_and_goods_hauled(lakeside):
    ops = lakeside["dossier"]["operations"]
    assert ops["tow_disabled_autos"] == 90
    assert ops["roadside_assistance"] == 10
    assert ops["repo_work"] is False

    goods = lakeside["dossier"]["goods_hauled"]
    assert goods["private_passenger_vehicles"] == 100
    assert goods["hazmat_goods"] is False


def test_radius_plain_and_annotated_bucket(lakeside):
    radius = lakeside["dossier"]["radius"]
    assert radius["lt50"] == 80
    # this bucket carries a note on the real QP ("20   200mi max") - it is
    # normalized into the "PCT (note)" shape rts_fill.py._radius() expects,
    # not dropped and not left as raw multi-space text.
    assert radius["51_300"] == "20 (200mi max)"
    assert radius["301_500"] is False


def test_ops_details(lakeside):
    od = lakeside["dossier"]["ops_details"]
    assert od["gross_revenue"] == 90000
    assert od["hours"] == "lt12"
    assert od["safety_chains_always"] is True
    assert od["written_vehicle_maintenance"] is True
    assert od["written_safety_program"] is True
    assert od["allow_passengers"] is False
    assert od["transportation_plates"] is False


def test_top_level_codes_and_signature(lakeside):
    d = lakeside["dossier"]
    assert d["sp_policy_code"] == "LAKES CAP tow new 8.3.26 CA 1 Century MP"
    assert d["source_code"] == "Ops mkt JV CC open MP"
    assert d["insured_signature"] == "Lakeside Towing LLC"


def test_meta_data_interstate(lakeside):
    assert lakeside["dossier"]["meta_data"]["interstate"] == "No"


def test_vehicle_totals(lakeside):
    totals = lakeside["dossier"]["vehicle_totals"]
    assert totals["power_units"] == 1
    assert totals["trailers"] == 0
    assert totals["stated_value"] == 50000


# ---------------------------------------------------------------- error handling


def test_malformed_pdf_raises_clear_error(tmp_path):
    fake = tmp_path / "not_really_a.pdf"
    fake.write_text("this is not a PDF file", encoding="utf-8")
    with pytest.raises(QPReadError):
        read_qp(str(fake))


def test_pdf_with_no_form_fields_raises_clear_error(tmp_path):
    import fitz
    doc = fitz.open()
    doc.new_page()
    blank = tmp_path / "blank.pdf"
    doc.save(str(blank))
    doc.close()
    with pytest.raises(QPReadError):
        read_qp(str(blank))


def test_missing_file_raises_clear_error(tmp_path):
    with pytest.raises(QPReadError):
        read_qp(str(tmp_path / "does_not_exist.pdf"))


# ---------------------------------------------------------------- merge semantics
# (precedent: app-form/scripts/fill_app.py deep_merge - vehicles by VIN,
# drivers by name, loss_runs by year - reused verbatim, not reinvented)


def test_merge_preserves_existing_data_qp_never_mentions(lakeside):
    existing = {
        "company": {"how_found_sierra": "referral from Bob"},
        "_identifier_notes": ["a human note the QP has no idea about"],
    }
    merged, conflicts = merge_into_dossier(existing, lakeside["dossier"])
    assert merged["company"]["dba"] == "Lakeside Towing LLC"
    assert merged["_identifier_notes"] == ["a human note the QP has no idea about"]
    # The dossier wins where the two disagree, and the disagreement is surfaced
    # rather than applied — see tests/test_qp_merge_safety.py for the rationale.
    assert merged["company"]["how_found_sierra"] == "referral from Bob"
    assert any("how_found_sierra" in c for c in conflicts)


def test_merge_matches_vehicles_by_vin_not_duplicate(lakeside):
    existing = {"vehicles": [{"vin": "5PVNJ8JN5G4S52079", "stated_value": 55000}]}
    merged, conflicts = merge_into_dossier(existing, lakeside["dossier"])
    assert len(merged["vehicles"]) == 1
    # The QP still says 50,000; the corrected 55,000 on file must survive it.
    assert merged["vehicles"][0]["stated_value"] == 55000
    assert any("stated_value" in c for c in conflicts)


def test_merge_matches_drivers_by_name_and_loss_runs_by_year(lakeside):
    existing = {
        "drivers": [{"name": "Salomon Lakeside", "license_state_note": "verified 2024"}],
        "loss_runs": [{"year": 2022, "unverified": True}],
    }
    merged, _ = merge_into_dossier(existing, lakeside["dossier"])
    assert len(merged["drivers"]) == 1
    assert merged["drivers"][0]["license_state_note"] == "verified 2024"
    assert merged["drivers"][0]["license"] == "B5697020"
    by_year = {r["year"]: r for r in merged["loss_runs"]}
    assert len(merged["loss_runs"]) == 5
    assert by_year[2022]["unverified"] is True
    assert by_year[2022]["policy_number"] == "73TRS123634"


def test_read_qp_is_read_only_and_never_touches_the_real_dossier():
    real_state = ROOT / "app-form" / "clients" / "lakeside-towing-llc" / "state.json"
    before = real_state.read_text(encoding="utf-8")
    read_qp(str(REAL_QP))
    after = real_state.read_text(encoding="utf-8")
    assert before == after
