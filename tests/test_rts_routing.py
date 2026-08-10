"""Which vendor app gets made — JC's routing rule, stated twice on 7.29:

    "If it's in California and it's a tow risk with one to nine power units,
     we make the RTS app. Since it's not in California, we'd only make the
     SP app."

Three conditions, all required. The interesting part is what happens when one
of them is unknown rather than false: an Arizona client is a decision, a client
whose state nobody recorded is not. Silence in that case reads as "no RTS
needed" and it isn't — so the rule reports its reason either way.
"""
from __future__ import annotations

import routing


def dossier(**over):
    base = {
        "company": {"state": "CA",
                    "location_address": "1 Main St, San Bernardino, CA 92404"},
        "operations": {"tow_disabled_autos": 90, "roadside_assistance": 10},
        "vehicle_totals": {"power_units": 3},
    }
    base.update(over)
    return base


def test_california_tow_risk_with_three_trucks_gets_the_rts():
    d = routing.rts_applies(dossier())
    assert d.applies is True


def test_outside_california_only_the_sp_app_is_made():
    d = routing.rts_applies(dossier(company={"state": "TX",
                                             "location_address": "1 Main St, Dallas, TX 75201"}))
    assert d.applies is False
    assert "California" in d.reason


def test_the_state_is_read_from_the_address_when_no_field_carries_it():
    # Real dossiers (Lakeside) have no company.state at all — the state only
    # exists inside the location address string.
    d = routing.rts_applies(dossier(company={
        "location_address": "7050 Perris Hill Rd, San Bernardino, CA 92404"}))
    assert d.applies is True


def test_ten_power_units_is_outside_the_program():
    d = routing.rts_applies(dossier(vehicle_totals={"power_units": 10}))
    assert d.applies is False
    assert "power unit" in d.reason


def test_nine_power_units_is_still_inside_it():
    assert routing.rts_applies(dossier(vehicle_totals={"power_units": 9})).applies is True


def test_one_power_unit_is_inside_it():
    assert routing.rts_applies(dossier(vehicle_totals={"power_units": 1})).applies is True


def test_a_risk_that_does_no_towing_is_not_a_tow_risk():
    d = routing.rts_applies(dossier(operations={"used_auto_hauling": 100},
                                    company={"first_named_insured": "Delta Auto Transport LLC",
                                             "location_address": "1 Main St, Fresno, CA 93701"}))
    assert d.applies is False
    assert "tow" in d.reason


# ------------------------------- classifying the risk from an incomplete file

def test_towing_revenue_counts_when_the_operations_block_is_empty():
    # Real dossiers arrive this way: Falcon Ridge has operations = null and its
    # towing shows up only as revenue (police rotations, motor clubs).
    d = routing.rts_applies(dossier(
        operations=None,
        revenue_sources={"police_rotations": 30, "motor_clubs": True}))
    assert d.applies is True


def test_the_business_name_classifies_a_file_with_nothing_else_in_it():
    # Ridgeline Towing & Recovery: no operations, no revenue, no description.
    # A human reads the name and knows. So does this — but it says that it did.
    d = routing.rts_applies(dossier(
        operations=None, revenue_sources=None,
        company={"first_named_insured": "Ridgeline Towing & Recovery Inc",
                 "location_address": "1 Main St, Fresno, CA 93701"}))
    assert d.applies is True
    assert "name" in d.reason.lower()


def test_a_file_with_no_signal_at_all_is_a_gap():
    d = routing.rts_applies(dossier(
        operations=None, revenue_sources=None,
        company={"first_named_insured": "Delta Holdings LLC",
                 "location_address": "1 Main St, Fresno, CA 93701"}))
    assert d.applies is False
    assert d.unknown is True


def test_hauling_in_the_name_is_not_towing():
    d = routing.rts_applies(dossier(
        operations=None, revenue_sources=None,
        company={"first_named_insured": "Delta Auto Hauling LLC",
                 "location_address": "1 Main St, Fresno, CA 93701"}))
    assert d.applies is False


def test_an_unknown_state_is_reported_not_assumed():
    d = routing.rts_applies(dossier(company={}))
    assert d.applies is False
    assert d.unknown is True
    assert "state" in d.reason.lower()


def test_no_power_unit_count_is_unknown_not_zero():
    d = routing.rts_applies(dossier(vehicle_totals={}))
    assert d.applies is False
    assert d.unknown is True


def test_a_counted_fleet_of_zero_is_a_decision_not_a_gap():
    # Zero trucks on a towing application is a real answer, and a wrong one —
    # but it is not missing information.
    d = routing.rts_applies(dossier(vehicle_totals={"power_units": 0}))
    assert d.applies is False
    assert d.unknown is False


def test_vehicles_are_counted_when_no_total_was_computed():
    d = routing.rts_applies(dossier(vehicle_totals={},
                                    vehicles=[{"vin": "a"}, {"vin": "b"}]))
    assert d.applies is True
