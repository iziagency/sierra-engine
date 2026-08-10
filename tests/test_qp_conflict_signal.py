"""A flagged conflict must be a real disagreement, not a formatting difference.

Running the real Lakeside QP produced 12 "disagreements" of which only 2 were
genuine — the rest were the same value written differently (85-0604178 vs
85.0604178, dashes vs dots in a phone, MM/DD/YYYY vs M.D.YY, upper vs lower
case). That is the Tita/JP failure JC described on the 7.27 call: hand a broker
ten false alerts and they stop reading the one that matters. The real finding
buried in that noise was a loss-run effective date of 8.25.25 vs 8.3.25.
"""
from __future__ import annotations

import qp_read


def conflicts_for(on_file, from_qp, key="company", field="x"):
    _, conflicts = qp_read.merge_into_dossier(
        {key: {field: on_file}}, {key: {field: from_qp}})
    return conflicts


# --- same value, different formatting: must be silent -----------------------

def test_fein_dash_vs_dot_is_not_a_conflict():
    assert conflicts_for("85-0604178", "85.0604178") == []


def test_phone_dash_vs_dot_is_not_a_conflict():
    assert conflicts_for("909-685-9794", "909.685.9794") == []


def test_phone_with_parens_is_not_a_conflict():
    assert conflicts_for("(909) 685-9794", "909.685.9794") == []


def test_case_difference_is_not_a_conflict():
    assert conflicts_for("owner/driver", "Owner/Driver") == []


def test_surrounding_whitespace_is_not_a_conflict():
    assert conflicts_for("Lakeside Towing LLC", "  Lakeside Towing LLC ") == []


def test_number_as_text_vs_number_is_not_a_conflict():
    assert conflicts_for(20, "20") == []
    assert conflicts_for(50000, "50,000") == []


def test_same_date_in_two_formats_is_not_a_conflict():
    assert conflicts_for("08/03/2023", "8.3.23") == []


def test_same_date_range_in_two_formats_is_not_a_conflict():
    assert conflicts_for("08/03/2023 - 08/03/2024", "8.3.23 - 8.3.24") == []


def test_parenthetical_annotation_on_the_same_number_is_not_a_conflict():
    assert conflicts_for(20, "20 (200mi max)") == []


# --- genuinely different values: must still be reported ---------------------

def test_a_different_amount_is_still_reported():
    c = conflicts_for(55000, 50000, key="coverages", field="total_stated_value")
    assert len(c) == 1 and "55000" in c[0] and "50000" in c[0]


def test_a_different_date_in_a_range_is_still_reported():
    c = conflicts_for("8.3.24 - 8.25.25", "8.3.24 - 8.3.25")
    assert len(c) == 1, "the one real finding on the Lakeside QP must survive"


def test_a_different_phone_number_is_still_reported():
    assert len(conflicts_for("909-685-9794", "909.111.2222")) == 1


def test_a_different_fein_is_still_reported():
    assert len(conflicts_for("85-0604178", "99-1234567")) == 1


def test_a_longer_carrier_name_is_reported_since_it_may_matter():
    # Not a formatting variant: one string genuinely carries a word the other
    # lacks. Cheap for a broker to dismiss, and a carrier's legal name is the
    # kind of thing an underwriter checks.
    assert len(conflicts_for("Obsidian Specialty Insurance",
                             "Obsidian Specialty Insurance Company")) == 1
