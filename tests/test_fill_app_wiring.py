"""Wiring tests for app-form/scripts/fill_app.py: the Filler class routes
phone/date/dash-strip formatting through shared/formatting.py, and drops
placeholder answers ("N/A", "unknown"...) instead of printing them.

These exercise Filler directly (no PDF template needed - Filler.run() only
ever touches the in-memory `values` dict; fill_pdf() is what opens the real
template, and it is not part of this change).
"""
from __future__ import annotations

from fill_app import Filler


def _run(data: dict) -> dict:
    filler = Filler(data)
    filler.run()
    return filler.values


class TestPhoneFields:
    def test_contact_cell_is_dotted(self):
        values = _run({"company": {"contact_cell": "909-685-9794"}})
        assert values["p1_contact_cell_phone"] == "909.685.9794"

    def test_office_phone_is_dotted(self):
        values = _run({"company": {"office_phone": "(909) 685-9794"}})
        assert values["p1_office_phone"] == "909.685.9794"


class TestDateFields:
    def test_current_auto_expires_is_mdyy(self):
        values = _run({"company": {"current_auto_expires": "8/3/2026"}})
        assert values["p1_current_auto_policy_expires"] == "8.3.26"

    def test_current_wc_expires_is_mdyy(self):
        values = _run({"company": {"current_wc_expires": "11/01/2026"}})
        assert values["p1_current_wc_policy_expires"] == "11.1.26"


class TestStateFilingNumberDashStrip:
    def test_dash_is_removed(self):
        values = _run({"company": {"state_filing_number": "CA-123456"}})
        assert values["p1_state_filing_number"] == "CA123456"


class TestDriverDetails:
    def test_birthday_is_formatted_and_license_dash_is_stripped(self):
        values = _run({"drivers": [
            {"name": "Jane Owner", "state": "CA", "license": "D-1234567",
             "birthday": "03/04/1985", "position": "owner/driver"},
        ]})
        details = values["p7_drv01_details"]
        assert "3.4.85" in details
        assert "D1234567" in details
        assert "D-1234567" not in details

    def test_date_of_hire_month_year_only_is_left_alone(self):
        # date_of_hire can be MM/YYYY (no day) - format_date cannot parse
        # that shape and must not silently swallow it.
        values = _run({"drivers": [{"name": "Jane Owner", "date_of_hire": "01/2012"}]})
        assert "hired 01/2012" in values["p7_drv01_details"]


class TestBlankIfUnknown:
    def test_placeholder_answer_is_omitted_not_printed(self):
        values = _run({"company": {"dash_cameras_brand": "unknown"}})
        assert "p1_vehicle_dash_cameras_brand" not in values

    def test_real_answer_still_goes_through(self):
        values = _run({"company": {"dash_cameras_brand": "Samsara"}})
        assert values["p1_vehicle_dash_cameras_brand"] == "Samsara"
