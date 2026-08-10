"""Date RANGES have to obey JC's format too, not just single dates.

Seen in a generated app: `07/12/2025 - 07/12/2026`. Leading zeros and slashes —
both of the things he ruled out on the 7.27 call — because the normaliser only
understood a single date and a policy period is two. Every loss-run effective
period in the file went out wrong.

His rule, verbatim: "no leading zeros, no slashes, only dots, two digit year".
It applies to whatever carries a date, including both ends of a period.
"""
from __future__ import annotations

import formatting as f


def test_a_policy_period_is_normalised_on_both_ends():
    assert f.format_date("07/12/2025 - 07/12/2026") == "7.12.25 - 7.12.26"


def test_the_loss_run_periods_already_on_file_are_normalised():
    assert f.format_date("08/03/2023 - 08/03/2024") == "8.3.23 - 8.3.24"


def test_an_already_correct_range_is_left_alone():
    assert f.format_date("7.12.25 - 7.12.26") == "7.12.25 - 7.12.26"


def test_a_mixed_range_is_fixed():
    assert f.format_date("8.3.24 - 08/25/2025") == "8.3.24 - 8.25.25"


def test_other_dash_styles_are_accepted():
    for sep in ("-", " - ", " – ", " to ", " through "):
        got = f.format_date(f"07/12/2025{sep}07/12/2026")
        assert got == "7.12.25 - 7.12.26", (sep, got)


def test_a_single_date_still_works():
    assert f.format_date("07/12/2025") == "7.12.25"


def test_a_range_with_one_unparseable_end_is_not_half_invented():
    # Better to hand back nothing than a period with one real end and one guess.
    assert f.format_date("07/12/2025 - sometime next year") is None


def test_a_non_date_is_still_rejected():
    assert f.format_date("Sierra") is None
    assert f.format_date("") is None
    assert f.format_date(None) is None
