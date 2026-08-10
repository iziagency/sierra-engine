"""Which vendor application a submission earns.

JC's rule, stated twice on the 7.29 call:

    "If it's in California and it's a tow risk with one to nine power units,
     we make the RTS app. Since it's not in California, we'd only make the
     SP app."

Phase 1 is exactly two documents — the Sierra Pacific CAP app, always, and the
RTS/Progressive Excel when this rule says so. Everything else is phase 2.

The rule distinguishes "no" from "don't know". A Texas client does not get an
RTS and that is settled; a client whose state nobody recorded does not get one
either, but that is a hole in the file, and the broker has to hear about it.
Reporting both the same way is how a submission quietly goes out short.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# Operation shares on the CAP app that mean "this risk tows". Auto hauling and
# transport are their own class of business and are deliberately absent.
TOW_OPERATIONS = (
    "tow_disabled_autos", "roadside_assistance", "private_property_impounds",
    "police_impounds", "accident_recovery", "lien_sales", "towing_not_for_hire",
)

# Revenue lines that only a tow operation earns.
TOW_REVENUE = ("motor_clubs", "police_rotations", "impounds",
               "private_party_incoming_calls")

# "Recovery" and "roadside" belong; "hauling" and "transport" are the auto
# transport class this rule exists to exclude.
TOW_WORDS = re.compile(r"\b(tow|towing|wrecker|roadside|recovery)\b", re.I)
NON_TOW_WORDS = re.compile(
    r"\b(hauling|haulers?|transport|transportation|logistics|trucking|"
    r"freight|delivery|courier)\b", re.I)

RTS_MAX_POWER_UNITS = 9


@dataclass(frozen=True)
class Decision:
    applies: bool
    reason: str
    unknown: bool = False       # blocked by a gap in the file, not by the rule


def state_of(company: dict, location: dict | None = None) -> str:
    """Two-letter state. Real dossiers often carry it only inside an address
    string — Lakeside has no `state` field at all.

    The candidates are tried until one YIELDS a state, not until one is
    non-empty. Hartley Towing types "See location schedule" into the
    location address; chaining with `or` let that placeholder win and the real
    address, two fields away, was never read.
    """
    company = company or {}
    direct = str(company.get("state") or "").strip().upper()
    if len(direct) == 2:
        return direct
    for addr in (company.get("location_address"),
                 company.get("mailing_address"),
                 (location or {}).get("address")):
        m = re.search(r",\s*([A-Z]{2})\s+\d{5}", str(addr or "").upper())
        if m:
            return m.group(1)
    return ""


def _power_units(dossier: dict):
    """Count of power units, or None when the file never says."""
    totals = dossier.get("vehicle_totals") or {}
    if totals.get("power_units") is not None:
        return int(totals["power_units"])
    vehicles = dossier.get("vehicles")
    return len(vehicles) if vehicles else None


def _tows(dossier: dict) -> tuple[bool | None, str]:
    """Is this a tow risk? Returns (verdict, how we know); None = can't tell.

    The structured block is the best evidence and the least reliable to expect:
    `operations` is null on real dossiers (Falcon Ridge, Ridgeline) because the
    source paperwork never filled that grid. So the classification falls back
    through weaker signals, and names which one it used — an RTS produced off
    the company name alone is a defensible call, but the broker should see that
    that is what it was.
    """
    ops = dossier.get("operations") or {}
    if any(ops.get(k) for k in TOW_OPERATIONS):
        return True, "operations"
    if ops:                       # the grid exists and says no towing
        return False, "operations"

    rev = dossier.get("revenue_sources") or {}
    if any(rev.get(k) for k in TOW_REVENUE):
        return True, "revenue"

    company = dossier.get("company") or {}
    text = " ".join(str(company.get(k) or "") for k in
                    ("first_named_insured", "dba"))
    if TOW_WORDS.search(text):
        return True, "name"
    described = str(dossier.get("overall_description") or "")
    if TOW_WORDS.search(described):
        return True, "description"
    # A name that says "hauling" is evidence of the other class of business. A
    # name that says nothing either way is not evidence of anything — "Delta
    # Holdings LLC" tows for all we know, and calling that a no would drop the
    # vendor app on a file that needed it.
    if NON_TOW_WORDS.search(text) or NON_TOW_WORDS.search(described):
        return False, "name"
    return None, ""


def rts_applies(dossier: dict) -> Decision:
    state = state_of(dossier.get("company") or {}, dossier.get("location"))
    if not state:
        return Decision(False, "No state on file, so the RTS routing rule "
                               "can't be applied — where is this risk located?",
                        unknown=True)
    if state != "CA":
        return Decision(False, f"{state}, not California — Sierra Pacific app only.")

    tows, basis = _tows(dossier)
    if tows is None:
        return Decision(False, "Nothing on file says what this risk does, so "
                               "the RTS routing rule can't be applied.",
                        unknown=True)
    if not tows:
        return Decision(False, "Not a towing risk — the RTS program is a tow "
                               "program.")

    units = _power_units(dossier)
    if units is None:
        return Decision(False, "No power unit count on file, so the 1-9 "
                               "eligibility can't be checked.", unknown=True)
    if units == 0:
        return Decision(False, "Zero power units on a towing application.")
    if units > RTS_MAX_POWER_UNITS:
        return Decision(False, f"{units} power units — over the {RTS_MAX_POWER_UNITS} "
                               f"the RTS program takes.")

    inferred = {"name": " (classified from the business name)",
                "description": " (classified from the business description)"}
    return Decision(True, f"California tow risk, {units} power unit"
                          f"{'s' if units != 1 else ''}"
                          f"{inferred.get(basis, '')}.")


# A human naming the document, in either order and in either language: "RTS Prog
# app", "CAP RTS PROG EXCEL APP PREP", "the Progressive supplemental", "el excel
# de RTS". The vendor token alone is not enough — "the RTS is wrong" is talk
# about a document, not a request for one — so an artifact word has to sit near
# it, and neither a sentence end nor a line break may separate them.
_VENDOR = r"(?:rts|progressive|prog)"
_ARTIFACT = r"(?:app|apps|excel|workbook|supp|suppl|supplemental)"
RTS_REQUEST = re.compile(
    rf"\b{_VENDOR}\b[^.\n]{{0,40}}?\b{_ARTIFACT}\b"
    rf"|\b{_ARTIFACT}\b[^.\n]{{0,40}}?\b{_VENDOR}\b", re.I)


def asked_for_rts(text: str | None) -> bool:
    """Did a human name the RTS/Progressive application in this message?

    This exists because the routing rule and a direct instruction are different
    kinds of statement. The rule answers "what does this risk earn on its own",
    which is the right question when nobody said anything. It was never meant to
    refuse a licensed broker who asked for the document by name — and refusing
    one in silence is the automation making a decision, which is the one thing
    Sierra's constraint forbids.

    Real caption, #ai-testings 8.6.26: "Prep new CAP app + RTS Prog app", on a
    risk the rule correctly excludes (Arizona, general freight). Both facts are
    true at once, so the answer is to build it and say why it was in doubt.
    """
    return bool(text and RTS_REQUEST.search(text))
