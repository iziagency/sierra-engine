"""TDD for shared/formatting.py - JC's 7.27.26 call, output-formatting rules.

Dates: "no leading zeros, no slashes, only dots, two digit year" -> 9.10.26.
Phones: "the number format... matches the date... 760.555.0164."
Blanks beat filler; unknowns are omitted, not guessed.
"""
from __future__ import annotations

import datetime

import pytest

import formatting


class TestFormatDate:
    def test_slash_date_with_four_digit_year(self):
        assert formatting.format_date("3/10/2025") == "3.10.25"

    def test_slash_date_with_two_digit_year(self):
        assert formatting.format_date("03/10/25") == "3.10.25"

    def test_iso_date(self):
        assert formatting.format_date("2025-03-10") == "3.10.25"

    def test_already_dotted_is_idempotent(self):
        assert formatting.format_date("3.10.25") == "3.10.25"

    def test_dash_separated_still_accepted(self):
        assert formatting.format_date("3-10-2025") == "3.10.25"

    def test_leading_zeros_are_dropped(self):
        assert formatting.format_date("03/04/1985") == "3.4.85"

    def test_two_digit_year_that_would_land_in_the_future_resolves_to_1900s(self):
        # "today" in this project's data is 2026, so a bare "30" landing on
        # 2030 is still in the future -> it must be read as 1930.
        assert formatting.format_date("1/1/30") == "1.1.30"

    def test_two_digit_year_in_the_past_resolves_to_2000s(self):
        assert formatting.format_date("1/1/20") == "1.1.20"

    def test_bare_date_object(self):
        assert formatting.format_date(datetime.date(2026, 7, 27)) == "7.27.26"

    def test_bare_datetime_object(self):
        assert formatting.format_date(datetime.datetime(2025, 3, 10, 14, 30)) == "3.10.25"

    def test_none_is_blank(self):
        assert formatting.format_date(None) is None

    def test_empty_string_is_blank(self):
        assert formatting.format_date("") is None

    def test_whitespace_only_is_blank(self):
        assert formatting.format_date("   ") is None

    def test_garbage_text_is_blank_not_a_crash(self):
        assert formatting.format_date("not a date") is None

    def test_invalid_calendar_date_is_blank_not_invented(self):
        assert formatting.format_date("2/30/2026") is None  # Feb 30 does not exist

    def test_invalid_month_is_blank(self):
        assert formatting.format_date("13/1/2025") is None

    def test_never_crashes_on_unexpected_types(self):
        # defensive: JC's rule ("never invent, never crash") is absolute even
        # for input shapes nobody documented
        assert formatting.format_date(12345) is None
        assert formatting.format_date([1, 2, 3]) is None


class TestFormatPhone:
    def test_dashed(self):
        assert formatting.format_phone("909-685-9794") == "909.685.9794"

    def test_parens_and_dash(self):
        assert formatting.format_phone("(909) 685-9794") == "909.685.9794"

    def test_plus_one_country_code(self):
        assert formatting.format_phone("+1 909 685 9794") == "909.685.9794"

    def test_bare_digits(self):
        assert formatting.format_phone("9096859794") == "909.685.9794"

    def test_already_dotted_is_idempotent(self):
        assert formatting.format_phone("909.685.9794") == "909.685.9794"

    def test_jcs_own_example(self):
        # "the number format just FYI matches the date it would be 760.555.0164"
        assert formatting.format_phone("7605550164") == "760.555.0164"

    def test_extension_ext_form(self):
        assert formatting.format_phone("909-685-9794 ext 123") == "909.685.9794 x123"

    def test_extension_x_form(self):
        assert formatting.format_phone("909-685-9794 x123") == "909.685.9794 x123"

    def test_none_is_blank(self):
        assert formatting.format_phone(None) is None

    def test_empty_string_is_blank(self):
        assert formatting.format_phone("") is None

    def test_not_a_phone_is_returned_unchanged(self):
        # Decision: a value that isn't recognisably a 10-digit US number is
        # left alone rather than mangled or blanked - reformatting is this
        # function's job, judging the data wrong is not.
        assert formatting.format_phone("ask the owner") == "ask the owner"

    def test_too_few_digits_is_returned_unchanged(self):
        assert formatting.format_phone("555-0164") == "555-0164"

    def test_too_many_digits_is_returned_unchanged(self):
        assert formatting.format_phone("123-456-789012") == "123-456-789012"

    def test_numeric_input_is_supported(self):
        assert formatting.format_phone(9096859794) == "909.685.9794"


class TestStripCosmeticDashes:
    def test_removes_dash(self):
        assert formatting.strip_cosmetic_dashes("D-1234567") == "D1234567"

    def test_no_dash_is_unchanged(self):
        assert formatting.strip_cosmetic_dashes("B5697020") == "B5697020"

    def test_dash_with_surrounding_spaces_collapses_cleanly(self):
        assert formatting.strip_cosmetic_dashes("CA - 123456") == "CA 123456"

    def test_multiple_dashes(self):
        assert formatting.strip_cosmetic_dashes("A-B-C-123") == "ABC123"

    def test_none_is_blank(self):
        assert formatting.strip_cosmetic_dashes(None) is None

    def test_empty_string_is_blank(self):
        assert formatting.strip_cosmetic_dashes("") is None


class TestBlankIfUnknown:
    @pytest.mark.parametrize("token", [
        "N/A", "n/a", "NA", "unknown", "Unknown", "TBD", "tbd", "pending",
        "None", "null", "--", "?", "???",
    ])
    def test_known_placeholder_tokens_become_blank(self, token):
        assert formatting.blank_if_unknown(token) is None

    def test_none_stays_none(self):
        assert formatting.blank_if_unknown(None) is None

    def test_empty_string_becomes_blank(self):
        assert formatting.blank_if_unknown("") is None

    def test_whitespace_only_becomes_blank(self):
        assert formatting.blank_if_unknown("   ") is None

    def test_real_value_is_returned_unchanged(self):
        assert formatting.blank_if_unknown("Samsara") == "Samsara"

    def test_real_value_is_stripped(self):
        assert formatting.blank_if_unknown("  Samsara  ") == "Samsara"

    def test_zero_is_not_unknown(self):
        # 0 vehicles / 0 claims is a real, meaningful answer - never blank it
        assert formatting.blank_if_unknown(0) == 0

    def test_false_is_not_unknown(self):
        assert formatting.blank_if_unknown(False) is False

    def test_substring_match_is_not_enough(self):
        # only an exact (case-insensitive) match is a placeholder; a real
        # value that merely contains one must survive
        assert formatting.blank_if_unknown("TBD Logistics LLC") == "TBD Logistics LLC"


class TestComposition:
    def test_unknown_phone_answer_ends_up_blank_when_composed(self):
        # format_phone alone leaves a non-numeric answer untouched (its own
        # contract, tested above); blank_if_unknown is the separate layer
        # that recognises a placeholder - this is how the two fillers
        # compose them in practice (see write_labeled / Filler.set).
        raw = "N/A"
        assert formatting.blank_if_unknown(formatting.format_phone(raw)) is None
