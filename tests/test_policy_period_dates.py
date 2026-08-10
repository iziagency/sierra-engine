"""Policy periods obey the date rule too — both ends of them.

JC's rule is `M.D.YY`, no leading zeros, no slashes. `shared/formatting.py` has
handled ranges since the 7.27 pass, but two writers never called it: the loss
run grid on page 9 and the loss run request on page 10 both set the effective
dates straight from the dossier.

This is not hypothetical. Four of the six real client files on this machine
carry the wrong shape right now, Lakeside among them:

    p10_effective_dates_3 = '08/03/2023 - 08/03/2024'

Slashes and leading zeros, in the file JC has been reviewing.
"""
from __future__ import annotations

import fill_app


def periods(runs: list[dict]) -> dict:
    return fill_app.lossrun_page_values({"loss_runs": runs})


def test_the_request_grid_formats_both_ends_of_the_period():
    out = periods([{"year": 2024, "carrier": "Obsidian",
                    "effective_dates": "08/03/2023 - 08/03/2024"}])
    assert out["p10_effective_dates"] == "8.3.23 - 8.3.24"


def test_a_period_printed_without_spaces_is_still_split():
    # Exactly how the Obsidian loss run PDF prints it.
    out = periods([{"year": 2024, "effective_dates": "8/3/2024-8/25/2025"}])
    assert out["p10_effective_dates"] == "8.3.24 - 8.25.25"


def test_an_already_correct_period_is_left_alone():
    out = periods([{"year": 2024, "effective_dates": "8.3.24 - 8.3.25"}])
    assert out["p10_effective_dates"] == "8.3.24 - 8.3.25"


def test_prose_a_broker_typed_survives_rather_than_vanishing():
    # "blank beats wrong" applies to a value the formatter can parse. A note it
    # cannot parse is still the only thing anyone knows about that year, and
    # dropping it silently loses information the underwriter asked for.
    out = periods([{"year": 2024, "effective_dates": "see attached dec page"}])
    assert out["p10_effective_dates"] == "see attached dec page"


def test_every_year_in_the_grid_is_formatted_not_just_the_first():
    out = periods([
        {"year": 2025, "effective_dates": "08/03/2024 - 08/03/2025"},
        {"year": 2024, "effective_dates": "08/03/2023 - 08/03/2024"},
        {"year": 2023, "effective_dates": "08/03/2022 - 08/03/2023"},
    ])
    assert out["p10_effective_dates"] == "8.3.24 - 8.3.25"
    assert out["p10_effective_dates_2"] == "8.3.23 - 8.3.24"
    assert out["p10_effective_dates_3"] == "8.3.22 - 8.3.23"


def test_a_missing_period_writes_nothing_at_all():
    # Empties are dropped upstream so a blank cell stays blank rather than
    # being written as "".
    out = periods([{"year": 2024, "carrier": "Obsidian"}])
    assert "p10_effective_dates" not in out
