"""The file's own history is never the model's to return.

Found live on 8.1.26. A broker replied in a known thread with "el FEIN es
88-3410999" — no correction keyword in it, so the drop took the update path:
the dossier is handed to the model with every `_`-prefixed key stripped out,
and whatever comes back is written to state.json as the new file.

`_changelog` survived only because it is re-attached by hand a few lines later.
`_red_flags` and `_identifier_notes` were not, so they were silently deleted:
Falcon Ridge went from twenty findings on file to one, Shoreline from two to
zero. The findings had been announced in Slack and were now unrecoverable —
exactly the failure JC reported on 7.29 ("16 flags in the channel and none on
file"), in a path the earlier fix never touched.

The rule: a model returns CLIENT DATA. Everything under `_` is the engine's own
record of what happened — history, findings, reading notes — and it is carried
across untouched, never round-tripped through a prompt.
"""
from __future__ import annotations

import process_drop as pd

ON_FILE = {
    "company": {"fein": "88-4415902", "first_named_insured": "Falcon Ridge Towing LLC"},
    "vehicles": [{"vin": "1FDUF5GT2PEC81437", "year": 2021}],
    "_red_flags": ["cash-settled accident never reported to the carrier",
                   "radius 100mi vs the profile's 50"],
    "_identifier_notes": ["VIN 1FDUF5GT2PEC81437 fails its check digit"],
    "_changelog": [{"ts": "2026-07-29 17:32", "op": "add", "hash": "abc"}],
}

# What a model hands back: client data only, no `_` keys — it never saw them.
FROM_MODEL = {
    "company": {"fein": "88-3410999", "first_named_insured": "Falcon Ridge Towing LLC"},
    "vehicles": [{"vin": "1FDUF5GT2PEC81437", "year": 2021}],
}


def test_findings_survive_a_model_round_trip():
    out = pd.carry_history(ON_FILE, dict(FROM_MODEL))
    assert out["_red_flags"] == ON_FILE["_red_flags"]


def test_reading_notes_survive_too():
    out = pd.carry_history(ON_FILE, dict(FROM_MODEL))
    assert out["_identifier_notes"] == ON_FILE["_identifier_notes"]


def test_the_change_the_broker_asked_for_still_lands():
    out = pd.carry_history(ON_FILE, dict(FROM_MODEL))
    assert out["company"]["fein"] == "88-3410999"


def test_anything_the_fresh_pass_added_wins_over_the_old_copy():
    # A pass that legitimately produced new findings keeps them; this helper
    # restores what was dropped, it does not overwrite live work.
    fresh = dict(FROM_MODEL, _red_flags=["a brand new finding"])
    out = pd.carry_history(ON_FILE, fresh)
    assert out["_red_flags"] == ["a brand new finding"]


def test_a_first_drop_has_nothing_to_carry():
    out = pd.carry_history(None, dict(FROM_MODEL))
    assert "_red_flags" not in out
    assert out["company"]["fein"] == "88-3410999"


def test_every_underscore_key_is_covered_not_a_hand_picked_list():
    # The bug was a hand-maintained list of what to preserve: _changelog was on
    # it, _red_flags was not. Whatever the engine stores under `_` is history.
    on_file = dict(ON_FILE, _future_thing=["something added later"])
    out = pd.carry_history(on_file, dict(FROM_MODEL))
    assert out["_future_thing"] == ["something added later"]
