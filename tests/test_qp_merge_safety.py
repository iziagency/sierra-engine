"""A QP must never silently overwrite what is already on file.

The QP is a point-in-time snapshot. Lakeside's real dossier carries a stated
value of 55,000 that a broker corrected after the QP was produced; the QP still
says 50,000. Under plain "new wins" merge semantics, reading the QP would put
the stale figure back and nobody would know. JC's standing rule is that the
automation produces a draft and flags, never decisions — so a disagreement is
a question for the broker, not something to resolve silently either way.
"""
from __future__ import annotations

import qp_read


def test_fills_a_field_the_dossier_does_not_have():
    merged, conflicts = qp_read.merge_into_dossier(
        {"company": {"dba": "Lakeside Towing LLC"}},
        {"company": {"usdot_number": "3416380"}},
    )
    assert merged["company"]["usdot_number"] == "3416380"
    assert merged["company"]["dba"] == "Lakeside Towing LLC"
    assert conflicts == []


def test_does_not_overwrite_an_existing_different_value():
    merged, conflicts = qp_read.merge_into_dossier(
        {"coverages": {"total_stated_value": 55000}},
        {"coverages": {"total_stated_value": 50000}},
    )
    assert merged["coverages"]["total_stated_value"] == 55000, (
        "the corrected figure must survive a stale QP")
    assert len(conflicts) == 1
    text = conflicts[0]
    assert "total_stated_value" in text
    assert "55000" in text and "50000" in text


def test_agreeing_values_are_not_reported_as_conflicts():
    _, conflicts = qp_read.merge_into_dossier(
        {"company": {"fein": "85.0604178"}},
        {"company": {"fein": "85.0604178"}},
    )
    assert conflicts == []


def test_blank_in_the_qp_never_erases_a_known_value():
    merged, conflicts = qp_read.merge_into_dossier(
        {"company": {"current_auto_carrier": "Obsidian"}},
        {"company": {"current_auto_carrier": ""}},
    )
    assert merged["company"]["current_auto_carrier"] == "Obsidian"
    assert conflicts == []


def test_list_rows_fill_gaps_but_flag_disagreement():
    merged, conflicts = qp_read.merge_into_dossier(
        {"vehicles": [{"vin": "5PVNJ8JN5G4S52079", "year": 2016, "stated_value": 55000}]},
        {"vehicles": [{"vin": "5PVNJ8JN5G4S52079", "year": 2016, "stated_value": 50000,
                       "gvw": 25500}]},
    )
    veh = merged["vehicles"][0]
    assert veh["stated_value"] == 55000, "existing value wins"
    assert veh["gvw"] == 25500, "genuinely new detail is still added"
    assert any("stated_value" in c for c in conflicts)


def test_a_new_row_is_appended_not_merged_into_an_existing_one():
    merged, conflicts = qp_read.merge_into_dossier(
        {"vehicles": [{"vin": "AAA", "year": 2016}]},
        {"vehicles": [{"vin": "BBB", "year": 2022}]},
    )
    assert [v["vin"] for v in merged["vehicles"]] == ["AAA", "BBB"]
    assert conflicts == []


def test_conflict_paths_are_readable_for_a_broker():
    _, conflicts = qp_read.merge_into_dossier(
        {"company": {"office_phone": "909.685.9794"}},
        {"company": {"office_phone": "909.111.2222"}},
    )
    assert conflicts and conflicts[0].startswith("company.office_phone")
