"""Resolve a client's SP code against JC's book, instead of inventing one.

The SP code is the identity of a file — JC, 7.29: "we always want to use the
SP name because it's our unique identifier." It is assigned by Sierra Pacific
and lives in their Drive folder names; it is not something a formula can
derive after the fact.

The old `sp_name()` took five letters and appended "1". Against the real 3,975
folders that is wrong in both directions:

  * an existing client gets the wrong code — Brookfield is BROOK2, Nora's is
    NORAS with no digit at all, Maximum is SUMMI;
  * and the invented code usually belongs to SOMEONE ELSE — FALCO1 is Desert
    Valley Towing, RIDGE1 is Ridge Route Towing, SHORE1 is Coastal Towing.
    Filing under it puts one insured's paperwork in another insured's file.

The rule the data actually shows: five letters of the name, plus a digit only
when that stem is already taken. `BROOK` was Haul Works, so Brookfield
became `BROOK2`. So the book has to be read before a code can be chosen at all
— for an existing client to find theirs, for a new one to find the next free
number.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

STEM_LEN = 5

# Legal noise that must not shift the stem: "Nora's Towing Inc" and "Noras
# Towing, Inc." are one client and one code.
_NOISE = re.compile(r"\b(llc|l\.l\.c|inc|incorporated|corp|corporation|co|"
                    r"company|ltd|lp|llp|dba|the)\b", re.I)


@dataclass(frozen=True)
class Resolved:
    code: str
    existing: bool          # the code was read off an existing folder
    reason: str
    unverified: bool = False  # the book could not be read — treat with care


def stem_of(name: str) -> str:
    """First five letters of the business name, JC's convention."""
    cleaned = _NOISE.sub(" ", str(name or ""))
    letters = re.sub(r"[^A-Za-z]", "", cleaned).upper()
    return letters[:STEM_LEN] or "CLIENT"


def _norm(name: str) -> str:
    """Two spellings of one client collapse to one key.

    Both differences below are real, off the live book: the Drive holds "Noras
    Towing Inc" while the app says "Nora's Towing Inc", and "Maximum Towing and
    Transport" against "Maximum Towing & Transport". An apostrophe is deleted
    rather than spaced, or "amy s towing" never meets "amys towing".
    """
    cleaned = str(name or "").replace("'", "").replace("’", "")
    cleaned = re.sub(r"\s*&\s*", " and ", cleaned)
    cleaned = _NOISE.sub(" ", cleaned)
    return " ".join(re.sub(r"[^a-z0-9 ]", " ", cleaned.lower()).split())


def parse_folder(folder: str) -> tuple[str, str]:
    """`"BROOK2 Brookfield Towing LLC"` -> `("BROOK2", "Brookfield …")`."""
    head, _, rest = str(folder or "").strip().partition(" ")
    return head.upper(), rest.strip()


def resolve(name: str, book: list[str]) -> Resolved:
    """The code for this client: theirs if they are in the book, else the next
    free number on their stem.

    `book` is the list of client folder names across Prospects and Clients.
    An EMPTY book means the Drive could not be read, not that the client is
    new — the answer is flagged unverified rather than quietly minted.
    """
    stem = stem_of(name)
    if not book:
        return Resolved(f"{stem}1", False,
                        "the client list could not be read, so this code is a "
                        "guess — confirm it against Drive before filing",
                        unverified=True)

    target = _norm(name)
    taken: set[str] = set()
    for folder in book:
        code, folder_name = parse_folder(folder)
        if not code:
            continue
        taken.add(code)
        if folder_name and _norm(folder_name) == target:
            return Resolved(code, True, f"matches “{folder}” already in Drive")

    # New client: the first number this stem has not used. A bare stem with no
    # digit (HAMIL, NORAS) still occupies the stem.
    n = 1
    while f"{stem}{n}" in taken:
        n += 1
    return Resolved(f"{stem}{n}", False,
                    f"new to the book — {stem} "
                    + ("was taken, so this is the next free number"
                       if stem in taken or f"{stem}1" in taken
                       else "is unused"))
