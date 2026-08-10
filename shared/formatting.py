r"""Shared output-formatting rules for the CAP app and RTS/QP fillers.

JC dictated these on the 7.27.26 call, verbatim:
  * Dates: "no leading zeros, no slashes, only dots, two digit year" -> 9.10.26,
    never 09/10/2026.
  * Phones: "the number format just FYI matches the date it would be
    760.555.0164" - same dotted shape as a date.
  * No dashes where they aren't needed: "like see right there no dashes are
    needed as well" (an identifier-style field) and, on the driver schedule,
    "we don't need dashes right there."
  * Blanks beat filler: "see how it's adding slashes. You can just leave a
    blank. Just as a general rule, just leave blank."
  * Omit unknowns entirely: "if a vehicle dash camera is not known leave it
    out."

Before this module, the M.D.YY date formatter existed as four separate copies
(watcher/process_drop.py's m8_date, watcher/lossruns.py's m8, reports/rts_fill.py's
m8, reports/qp_build.py's m8) and phone formatting did not exist anywhere - a
phone number reached the filled PDF exactly as it was typed, dashes and all.
All four now delegate to format_date() below; none of their own call sites
changed behaviour, including the "no argument = today" default the two report
builders use to stamp a filename (filename-generation logic is untouched by
this change on purpose).

NOT used on the FEIN: reports/rts_fill.py inserts a dash into that field on
purpose (`re.sub(r"[.\s]", "-", ...)`, a FEIN is conventionally XX-XXXXXXX).
That line is left exactly as it was - see this change's final report for the
open question it raises.
"""
from __future__ import annotations

import datetime
import re

# ---------------------------------------------------------------- dates

# M/D/YYYY, MM/DD/YY, dash- or dot-separated, already-M.D.YY - one grammar for
# every "day comes before the year" spelling JC's team or a model might type.
_SLASH_DATE = re.compile(r"^\s*(\d{1,2})[./\-](\d{1,2})[./\-](\d{2,4})\s*$")
# ISO YYYY-MM-DD - the year comes first, so it needs its own pattern rather
# than a variant of the one above.
_ISO_DATE = re.compile(r"^\s*(\d{4})-(\d{1,2})-(\d{1,2})\s*$")

# Splits a policy period into its two ends. Only consulted once a single-date
# parse has already failed, so a bare hyphen is safe to accept here.
_RANGE_SEP = re.compile(r"\s*[-–]\s*|\s+to\s+|\s+through\s+", re.I)


def _format_range(s: str) -> "str | None":
    """`M.D.YY - M.D.YY` for a policy period, or None.

    One good end and one guess is worse than nothing: a half-invented policy
    period still reads as fact to an underwriter.
    """
    parts = [p for p in _RANGE_SEP.split(s) if p.strip()]
    if len(parts) != 2:
        return None
    left, right = (format_date(p) for p in parts)
    return f"{left} - {right}" if left and right else None


def format_date(value: "str | datetime.date | datetime.datetime | None") -> str | None:
    """Any date JC's team might type or a model might extract -> `M.D.YY`.

    Accepts M/D/YYYY, MM/DD/YY, ISO YYYY-MM-DD, an already-`M.D.YY` string, or
    a bare `date`/`datetime` object (lossruns.py and the two fillers sometimes
    already hold a parsed date rather than a string). Returns None for
    anything that is not a real calendar date - empty, None, "N/A", prose, a
    February 30th - so a blank cell reaches the document rather than a wrong
    or half-built one. Never raises.
    """
    if value is None:
        return None
    if isinstance(value, datetime.date):       # datetime.datetime is a date too
        return f"{value.month}.{value.day}.{str(value.year)[2:]}"

    s = str(value).strip()
    if not s:
        return None

    iso = _ISO_DATE.match(s)
    if iso:
        yr, mo, da = (int(x) for x in iso.groups())
    else:
        slashed = _SLASH_DATE.match(s)
        if not slashed:
            # Not one date — a policy period is two, and JC's rule applies to
            # both ends. Tried only after the single-date parse fails, so
            # "7-12-2025" stays one date instead of being torn in half, while
            # "8/3/2024-8/25/2025" (exactly how the Obsidian loss run prints it,
            # no spaces) splits correctly. Seen going out wrong in a generated
            # app as "07/12/2025 - 07/12/2026", slashes and leading zeros intact.
            return _format_range(s)
        mo, da, yr = (int(x) for x in slashed.groups())
        if yr < 100:
            # A two-digit year that would land in the future is last
            # century's, not next year's - nobody on a driver schedule was
            # born in 2031.
            this_year_2d = datetime.date.today().year % 100
            yr += 2000 if yr <= this_year_2d else 1900

    try:
        d = datetime.date(yr, mo, da)           # rejects e.g. Feb 30 outright
    except ValueError:
        return None                             # not a real calendar date - never invent one
    return f"{d.month}.{d.day}.{str(d.year)[2:]}"


# ---------------------------------------------------------------- phones

_EXTENSION = re.compile(r"\s*(?:ext\.?|extension|x)\s*(\d+)\s*$", re.I)


def format_phone(value) -> str | None:
    """A US phone number, however it was typed, in JC's dotted format.

    `909.685.9794` - the same dotted shape as a date, per JC's own example
    ("the number format... it would be 760.555.0164"). A leading US country
    code (+1 / 1) is dropped; a trailing extension is kept, appended as
    ` x1234`. None in, None out.

    Decision: a value that is not recognisable as a 10-digit US number (too
    few/too many digits, or no digits at all) is returned UNCHANGED rather
    than mangled or blanked. This function's job is to reformat a phone
    number, not to judge that some other kind of value is wrong - a name, a
    note, or a foreign number is not this function's business, and blanking
    it would destroy data that a human still needs to see. Blank-if-unknown
    placeholders ("N/A", "unknown"...) are a separate, explicit concern -
    see blank_if_unknown() below.
    """
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None

    ext = ""
    m = _EXTENSION.search(s)
    if m:
        ext = m.group(1)
        s = s[:m.start()].strip()

    digits = re.sub(r"\D", "", s)
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]                     # US country code
    if len(digits) != 10:
        return s or None                        # not a 10-digit US number - leave it alone

    formatted = f"{digits[0:3]}.{digits[3:6]}.{digits[6:10]}"
    return f"{formatted} x{ext}" if ext else formatted


# ---------------------------------------------------------------- dashes

def strip_cosmetic_dashes(value) -> str | None:
    """Drop hyphens used as pure visual separators from an identifier-style
    value - a driver's license, a membership/authority number: "we don't need
    dashes right there" (JC, 7.27 call).

    Only ever call this on a field that IS an identifier. A company name or
    address can legitimately contain a hyphen ("Stop-N-Go Towing"), and this
    function has no way to tell that apart from a cosmetic separator - that
    judgment belongs to the caller, field by field, not to this helper.

    Explicitly NOT used on the FEIN - reports/rts_fill.py inserts a dash into
    that field on purpose. See this change's final report for the open
    question that line raises.
    """
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None
    cleaned = s.replace("-", "")
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned or None


# ----------------------------------------------------------------- money

_LIMIT = re.compile(r"^\$?\s*([\d,.]+)\s*([kmb])?$", re.I)


def format_limit(value) -> str | None:
    """A coverage limit as an underwriter reads it: `$1M`, `$150K`.

    JC, 7.29 call: "$1M with a capital M, not lowercase." Dossiers carry these
    shorthands exactly as the broker typed them - "1m", "150k" - and they go
    straight onto a document an American underwriter reads, so the case is not
    cosmetic.

    The capital-M rule was dictated; applying the same case to K and B is this
    codebase's reading of it, for consistency across the same field. Anything
    that is not a plain number with an optional magnitude suffix is returned
    untouched - "CSL 1,000,000/2,000,000" is a real answer and must survive.
    """
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None
    m = _LIMIT.match(s)
    if not m:
        return s
    number, suffix = m.group(1), (m.group(2) or "").upper()
    return f"${number}{suffix}"


# ---------------------------------------------------------------- blanks

# Placeholder answers that LOOK like data but are not one. Lowercased,
# exact-match only: a real value that merely CONTAINS one of these words
# ("TBD Logistics LLC") must survive - only an answer that IS one is a
# placeholder.
_UNKNOWN_TOKENS = {
    "n/a", "na", "unknown", "tbd", "tbc", "pending", "none", "null",
    "--", "-", "?", "???", "ask client", "ask the client",
}


def blank_if_unknown(value):
    """A placeholder answer ("N/A", "unknown", "TBD"...) becomes a true blank,
    so the caller's existing "don't write a blank field" guard omits it
    entirely from the document - JC: "You can just leave a blank. Just as a
    general rule, just leave blank," rather than something that only LOOKS
    like a real answer.

    Only strings are ever considered "unknown": 0, False, [], {} are real,
    meaningful answers (0 vehicles, no claims) and are returned unchanged.
    """
    if isinstance(value, str):
        s = value.strip()
        if not s or s.lower() in _UNKNOWN_TOKENS:
            return None
        return s
    return value
