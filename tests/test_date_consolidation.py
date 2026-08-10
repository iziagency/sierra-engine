"""The four separate m8()/m8_date() copies now all delegate to
shared/formatting.format_date() - same observable behaviour, one
implementation. Also covers the loss-run claim-date ingest normalisation in
watcher/lossruns.py (apply_gospel), which used to leave date_of_loss exactly
as the model wrote it (M/D/YYYY) and let reports/rts_fill.py be the only
place that ever reformatted it, at write time.
"""
from __future__ import annotations

import datetime

import lossruns
import process_drop
import qp_build
import rts_fill


class TestProcessDropMDate:
    def test_still_parses_slash_dates(self):
        assert process_drop.m8_date("08/09/1981") == "8.9.81"

    def test_still_parses_dotted_dates(self):
        assert process_drop.m8_date("8.9.81") == "8.9.81"

    def test_not_a_date_is_none(self):
        assert process_drop.m8_date("N/A") is None

    def test_none_is_none(self):
        assert process_drop.m8_date(None) is None


class TestLossrunsM8:
    def test_formats_a_date_object(self):
        assert lossruns.m8(datetime.date(2026, 8, 3)) == "8.3.26"

    def test_none_is_none(self):
        assert lossruns.m8(None) is None


class TestQpBuildM8:
    def test_defaults_to_today_for_the_filename_stamp(self):
        # filename-generation behaviour is explicitly out of scope for this
        # change and must not move: no argument -> today, exactly as before
        today = datetime.date.today()
        expected = f"{today.month}.{today.day}.{str(today.year)[2:]}"
        assert qp_build.m8() == expected

    def test_formats_a_given_date(self):
        assert qp_build.m8(datetime.date(2026, 1, 5)) == "1.5.26"


class TestRtsFillM8:
    def test_defaults_to_today_for_the_filename_stamp(self):
        today = datetime.date.today()
        expected = f"{today.month}.{today.day}.{str(today.year)[2:]}"
        assert rts_fill.m8() == expected

    def test_formats_a_given_date(self):
        assert rts_fill.m8(datetime.date(2026, 1, 5)) == "1.5.26"


class TestLossrunClaimDateIngestNormalisation:
    """Decision: normalise date_of_loss at BOTH ingest (here) and write time
    (rts_fill._driver_claims, tested separately) - ingest is the fix going
    forward, write-time formatting is a safety net for dossiers written
    before this change. Both paths call the same formatting.format_date(), so
    a dossier that already has the right spelling is untouched either way.
    """

    def test_normalised_claim_reformats_date_of_loss(self):
        claim = {"date_of_loss": "3/10/2025", "type": "AUTO-Collision"}
        out = lossruns._normalised_claim(claim)
        assert out["date_of_loss"] == "3.10.25"
        assert out["type"] == "AUTO-Collision"  # other keys untouched

    def test_normalised_claim_keeps_unparseable_date_rather_than_dropping_it(self):
        claim = {"date_of_loss": "sometime in March", "type": "AUTO-Collision"}
        out = lossruns._normalised_claim(claim)
        assert out["date_of_loss"] == "sometime in March"

    def test_normalised_claim_passes_through_a_row_with_no_date(self):
        claim = {"type": "AUTO-Collision"}
        out = lossruns._normalised_claim(claim)
        assert out == claim

    def test_apply_gospel_normalises_claims_on_a_fresh_dossier(self):
        dossier = {"loss_runs": []}
        runs = [{
            "carrier": "Obsidian Specialty Insurance",
            "policy_number": "TARPX-CP-000000312-01",
            "effective_date": "8/3/2024",
            "expiration_date": "8/25/2025",
            "claim_count": 1,
            "total_incurred": 3484.64,
            "claims": [{
                "date_of_loss": "3/10/2025",
                "type": "AUTO-Auto Collision",
                "status": "Closed",
                "total_incurred": 3484.64,
                "driver_name": "Lakeside, Salomon",
                "description": "four-way stop collision",
            }],
        }]
        lossruns.apply_gospel(dossier, runs)
        entry = next(r for r in dossier["loss_runs"] if r["year"] == "2024")
        assert entry["claims"][0]["date_of_loss"] == "3.10.25"
