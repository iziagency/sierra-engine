"""A document asked for by name gets built, even when the routing rule says no.

Real message, #ai-testings, 8.6.26. JC posted five attachments with the caption
"Prep new CAP app + RTS Prog app". The client inside those attachments is County
Trucking, LLC — Scottsdale AZ, general freight with reefers and dry van.

What happened: the drop pipeline built the CAP app, then `build_vendor_apps`
asked the routing rule about the RTS and got "AZ, not California - Sierra Pacific
app only." No RTS. Reported as a routine routing note.

The rule is right about Arizona. What is wrong is that JC asked for the document
by name and the engine never acknowledged it: `build_vendor_apps` is not given
the message text, so an explicit instruction is indistinguishable from silence.
Sierra's own constraint is that the automation produces a draft and flags, never
decisions — and quietly overruling a licensed broker with a rule is a decision.

So: build what was asked for, and raise the conflict as a question.
"""
from __future__ import annotations

import routing


def test_the_real_caption_counts_as_asking_for_the_rts():
    assert routing.asked_for_rts("Prep new CAP app + RTS Prog app") is True


def test_the_shorthands_the_team_actually_types_count_too():
    for line in ("CAP app + RTS Prog app",
                 "NORAS1 CAP RTS PROG EXCEL APP PREP",
                 "please put together the Progressive supplemental",
                 "necesito el excel de RTS"):
        assert routing.asked_for_rts(line) is True, line


def test_everyday_channel_talk_is_not_a_request():
    for line in ("here's the app for Nora's",
                 "the FEIN is 12-3456789",
                 "on a call with a client, can you text me?",
                 ""):
        assert routing.asked_for_rts(line) is False, line


def _county_trucking() -> dict:
    """As it comes out of JC's five attachments: Arizona, general freight."""
    return {
        "sp_code": "PARKW4",
        "company": {
            "first_named_insured": "Parkway Trucking, LLC",
            "dba": "Parkway Trucking",
            "mailing_address": "8502 East Via de Ventura #122, Scottsdale, AZ 85258",
            "location_address": "1438 East Jackson St., Phoenix, AZ 85034",
        },
        # No `operations` grid: the paperwork behind this drop is SMS screenshots
        # and a DOT letter, so nothing filled that block. Free text lands in
        # overall_description, which is where the classifier looks for it.
        "overall_description": "General freight with reefers and dry van",
        "vehicles": [{"vin": "1M1PR4GY5TM100874"}, {}],
    }


def test_the_rule_still_says_no_for_arizona_freight():
    d = routing.rts_applies(_county_trucking())
    assert d.applies is False
    assert "not California" in d.reason


def test_asked_for_by_name_it_gets_built_and_the_conflict_is_flagged(monkeypatch):
    import process_drop as pd

    import rts_fill
    monkeypatch.setattr(rts_fill, "fill",
                        lambda slug: {"ok": True, "file": f"/x/{slug}.xlsx",
                                      "cells": 60, "dropped": [],
                                      "unknown_blanks": []})

    rts = pd.build_vendor_apps(_county_trucking(), "parkway-trucking-llc",
                               caption="Prep new CAP app + RTS Prog app")["rts"]

    assert rts["applies"] is True, "JC named the document; it gets built"
    assert rts["cells"] == 60
    assert rts["overridden"] is True, (
        "the broker has to learn that a rule was set aside on his word")
    assert "not California" in rts["reason"], (
        "the reason must still carry what the rule said, or the flag teaches "
        "the reader nothing")
    assert "not California" in rts["rule_said"], (
        "the rule's own words travel as their own field, so the Slack reply "
        "never has to take the reason string apart to quote them")
    assert "asked" not in rts["rule_said"], (
        "rule_said is the rule speaking, nothing else")


def test_without_the_caption_nothing_changes(monkeypatch):
    import process_drop as pd

    import rts_fill
    called = []
    monkeypatch.setattr(rts_fill, "fill", lambda slug: called.append(slug) or {})

    rts = pd.build_vendor_apps(_county_trucking(), "parkway-trucking-llc")["rts"]

    assert rts["applies"] is False
    assert rts.get("overridden") is not True
    assert called == [], "an Arizona freight risk still earns no RTS on its own"


def test_a_california_tow_risk_is_not_marked_as_an_override(monkeypatch):
    import process_drop as pd

    import rts_fill
    monkeypatch.setattr(rts_fill, "fill",
                        lambda slug: {"ok": True, "file": "/x/a.xlsx",
                                      "cells": 60, "dropped": [],
                                      "unknown_blanks": []})

    # A risk the rule says yes to on its own: California, and a tow operation
    # stated in the operations grid, which is the strongest evidence there is.
    # The name matters too — "Trucking" is in the list of words that mean freight
    # rather than towing, so Parkway Trucking's name had to go with it.
    data = _county_trucking()
    data["company"]["first_named_insured"] = "Nora's Towing Inc"
    data["company"]["dba"] = "Nora's Towing"
    data["company"]["mailing_address"] = "7679 Lemon Ave, Lemon Grove, CA 91945"
    data["company"]["location_address"] = "7679 Lemon Ave, Lemon Grove, CA 91945"
    data.pop("overall_description")
    data["operations"] = {"tow_disabled_autos": 60, "roadside_assistance": 40}

    rts = pd.build_vendor_apps(data, "nora-s-towing-inc",
                               caption="Prep new CAP app + RTS Prog app")["rts"]

    assert rts["applies"] is True
    assert rts.get("overridden") is not True, (
        "the rule agreed on its own; calling that an override would cry wolf")
