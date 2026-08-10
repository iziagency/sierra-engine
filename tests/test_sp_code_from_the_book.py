"""The SP code is looked up in JC's book, never invented from arithmetic.

Found 8.1.26 from a real work assignment Allyssa sent on Slack. `sp_name()`
took the first five letters and appended "1". JC's actual 3,975 folders say
otherwise:

    Brookfield Towing LLC   -> BROOK2   (we wrote BROOK1)
    Noras Towing Inc             -> NORAS    (we wrote NORAS1)
    Maximum Towing and Transp.  -> SUMMI    (we wrote SUMMI1)
    South West Towing           -> SOUTH5
    Onsight Towing Services AB  -> ONSIG2

And the invented ones were not merely unused — they belong to OTHER companies:
FALCO1 is Desert Valley Towing, RIDGE1 is Ridge Route Towing, SHORE1 is
Coastal Towing. Filing Falcon Ridge's app under FALCO1 puts one insured's
paperwork in another insured's file.

The rule visible in the data: five letters of the name, and a digit only when
that stem is already taken — BROOK was Haul Works, so Brookfield became
BROOK2. An existing client's code is READ; a new client takes the next free
number, which still requires reading the book first.
"""
from __future__ import annotations

import pytest

import spcode

# A slice of the real book, verbatim from the Drive listing.
BOOK = [
    "BROOK Haul Works",
    "BROOK2 Brookfield Towing LLC",
    "BROOK4 Capital Towing Booting & Recovery LLC",
    "BROOK6 Capital Limousine Inc",
    "NORAS Noras Towing Inc",
    "LAKES Lakeside Towing",
    "SUMMI Maximum Towing and Transport of Central Florida",
    "SOUTH5 South West Towing",
    "HAMIL Hartley Towing",
    "ONSIG2 Onsight Towing Services AB",
    "CARRS Carrs Towing",
    "FALCO-1 Desert Valley Towing",
    "FALCO1 Desert Valley Towing",
    "FALCO2 Desert Towing",
    "SHORE1 Coastal Towing",
    "SHORE3 Coast to Coast Towing and Storage",
    "SHORE7 Coastal Towing & Recovery",
]


# ------------------------------------------------- an existing client is read

@pytest.mark.parametrize("name,code", [
    ("Brookfield Towing, LLC", "BROOK2"),
    ("Nora's Towing Inc", "NORAS"),
    ("Maximum Towing & Transport of Central Florida LLC", "SUMMI"),
    ("Lakeside Towing LLC", "LAKES"),
    ("South West Towing", "SOUTH5"),
    ("Onsight Towing Services AB", "ONSIG2"),
])
def test_the_book_decides_not_the_arithmetic(name, code):
    assert spcode.resolve(name, BOOK).code == code


def test_a_match_reports_that_it_came_from_the_book():
    got = spcode.resolve("Brookfield Towing, LLC", BOOK)
    assert got.existing is True
    assert "BROOK2 Brookfield Towing LLC" in got.reason


# ------------------------------------ a new client takes the next free number

def test_a_new_client_never_reuses_a_taken_stem():
    # "Falcon Ridge Towing LLC": FALCO, FALCO1, FALCO2 are all spoken for and
    # belong to other companies.
    got = spcode.resolve("Falcon Ridge Towing LLC", BOOK)
    assert got.existing is False
    assert got.code == "FALCO3"


def test_the_next_free_number_skips_every_variant_of_the_stem():
    # SHORE1, SHORE3, SHORE7 exist; the next free digit is 2, not 8.
    got = spcode.resolve("Shoreline Towing & Recovery LLC", BOOK)
    assert got.code == "SHORE2"


def test_a_stem_nobody_has_used_gets_the_first_number():
    # The Coding Guide: "New assignments always carry the number: SUMMI1, not
    # SUMMI."
    got = spcode.resolve("Zephyr Towing LLC", BOOK)
    assert got.code == "ZEPHY1"


def test_a_bare_stem_in_the_book_still_blocks_the_stem():
    # HAMIL exists with no digit; a different Hamilton company cannot be HAMIL.
    got = spcode.resolve("Hamilton Brothers Recovery", BOOK)
    assert got.code == "HAMIL1"


# --------------------------------------------------------- name normalisation

def test_punctuation_and_suffixes_do_not_change_the_stem():
    for name in ("Nora's Towing Inc", "Noras Towing, Inc.", "NORAS TOWING INC"):
        assert spcode.resolve(name, BOOK).code == "NORAS", name


def test_a_short_name_is_not_padded_into_a_different_one():
    got = spcode.resolve("Ace Tow", BOOK)
    assert got.code.startswith("ACETO")


# ------------------------------------------------------------- the empty book

def test_with_no_book_it_says_so_rather_than_pretending():
    # An empty listing means the Drive could not be read — not that the client
    # is new. Guessing here is how one insured's work lands in another's file.
    got = spcode.resolve("Brookfield Towing, LLC", [])
    assert got.unverified is True
