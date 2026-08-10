"""«See location schedule» is a pointer, not an address.

Hartley Towing, from JC's real QP on 7.30.26. Their app types "See location
schedule" into the location address field — the actual address, `3615 Campbell
St, Riverside, CA 92509`, sits in the mailing field and on the schedule page.

`state_of` chained the candidates with `or`, which short-circuits on
truthiness: a non-empty placeholder won, the fallback never ran, no state was
found, and the RTS routing rule refused to decide on a plain California tow
risk. The engine was right to refuse — it genuinely did not know — but it had
the answer two fields away.

Candidates are now tried until one YIELDS a state, not until one is non-empty.
"""
from __future__ import annotations

import routing

REAL = "3615 Campbell St, Riverside, CA 92509"


def test_a_pointer_in_the_location_field_falls_through_to_the_mailing_one():
    assert routing.state_of({"location_address": "See location schedule",
                             "mailing_address": REAL}) == "CA"


def test_the_location_schedule_block_is_a_candidate_too():
    dossier = {"company": {"location_address": "See location schedule"},
               "location": {"address": REAL},
               "operations": {"tow_disabled_autos": 100},
               "vehicle_totals": {"power_units": 1}}
    assert routing.rts_applies(dossier).applies is True


def test_a_real_location_address_still_wins():
    assert routing.state_of({
        "location_address": "1 Main St, Oxnard, CA 93035",
        "mailing_address": "99 Elsewhere Rd, Dallas, TX 75201"}) == "CA"


def test_an_explicit_state_field_still_outranks_everything():
    assert routing.state_of({"state": "NV", "location_address": REAL}) == "NV"


def test_no_usable_address_anywhere_is_still_unknown():
    assert routing.state_of({"location_address": "See location schedule",
                             "mailing_address": "TBD"}) == ""


def test_hartley_now_routes(monkeypatch):
    # The whole point: a one-truck California tow risk gets its RTS.
    dossier = {
        "company": {"first_named_insured": "Wendy Hamilton",
                    "dba": "Hamilton's Towing",
                    "location_address": "See location schedule",
                    "mailing_address": REAL},
        "vehicles": [{"vin": "1GC4CYEG0FF656041", "year": 2015}],
        "vehicle_totals": {"power_units": 1},
    }
    d = routing.rts_applies(dossier)
    assert d.applies is True
    assert "1 power unit" in d.reason
