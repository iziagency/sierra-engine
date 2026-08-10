r"""Recover a VIN's check digit, and say plainly what that does and does not prove.

Brookfield's file carries `3HAMMAALCL555829` — sixteen characters where a VIN
has seventeen. Position 9 of a VIN is a check digit and may only be 0-9 or X, and
in that string position 9 holds `C`, so the missing character cannot be anywhere
after it. Assume the check digit itself is what was dropped and the rest lines up:
position 10 becomes `C`, the model-year code for 2012, matching the 2012
International on the schedule, and positions 12-17 become a clean six-digit
serial.

Position 9 is the one character in a VIN derived from the other sixteen
(ISO 3779), so it is computed, not guessed.

What this proves: the other sixteen characters are internally consistent with
exactly one check digit, and the decoded vehicle matches the schedule.

What it does NOT prove: that those sixteen were transcribed correctly in the
first place. A mistyped serial digit would simply produce a different check digit
and look just as consistent. So the result is a CANDIDATE for the broker to
confirm against the title or registration — never a value written into the app on
our own authority.
"""
from __future__ import annotations

import json
import sys
import urllib.request

# ISO 3779 transliteration. I, O and Q are excluded from VINs precisely so they
# cannot be confused with 1 and 0, so they have no value here.
VALUES = {
    **{str(d): d for d in range(10)},
    "A": 1, "B": 2, "C": 3, "D": 4, "E": 5, "F": 6, "G": 7, "H": 8,
    "J": 1, "K": 2, "L": 3, "M": 4, "N": 5, "P": 7, "R": 9,
    "S": 2, "T": 3, "U": 4, "V": 5, "W": 6, "X": 7, "Y": 8, "Z": 9,
}
WEIGHTS = [8, 7, 6, 5, 4, 3, 2, 10, 0, 9, 8, 7, 6, 5, 4, 3, 2]

# position 10 of a VIN, model year
YEAR_CODES = {"A": 2010, "B": 2011, "C": 2012, "D": 2013, "E": 2014, "F": 2015,
              "G": 2016, "H": 2017, "J": 2018, "K": 2019, "L": 2020, "M": 2021,
              "N": 2022, "P": 2023, "R": 2024, "S": 2025, "T": 2026,
              "1": 2001, "2": 2002, "3": 2003, "4": 2004, "5": 2005, "6": 2006,
              "7": 2007, "8": 2008, "9": 2009}


def check_digit(vin17: str) -> str:
    """The digit that belongs at position 9 of a 17-character VIN."""
    total = sum(VALUES[ch] * w for ch, w in zip(vin17.upper(), WEIGHTS))
    r = total % 11
    return "X" if r == 10 else str(r)


def is_valid(vin: str) -> bool:
    vin = vin.upper()
    if len(vin) != 17 or set("IOQ") & set(vin) or any(c not in VALUES for c in vin):
        return False
    return vin[8] == check_digit(vin)


def repair_missing_check_digit(vin16: str) -> dict:
    """Insert the computed check digit at position 9 of a 16-character VIN."""
    v = "".join(vin16.upper().split())
    out = {"input": v, "ok": False, "reason": "", "vin": "", "model_year": None}
    if len(v) != 16:
        out["reason"] = f"expected 16 characters, got {len(v)}"
        return out
    if set("IOQ") & set(v):
        out["reason"] = "contains I, O or Q, which VINs never use — this is a " \
                        "transcription error, not a missing character"
        return out
    if v[8] in VALUES and v[8].isdigit():
        out["reason"] = (f"position 9 already holds a digit ({v[8]}), so the missing "
                         f"character is elsewhere and cannot be computed")
        return out

    body = v[:8] + "0" + v[8:]          # placeholder, weight at position 9 is zero
    cd = check_digit(body)
    vin = v[:8] + cd + v[8:]
    out.update(ok=True, vin=vin, model_year=YEAR_CODES.get(vin[9]))
    return out


def decode(vin: str) -> dict:
    url = (f"https://vpic.nhtsa.dot.gov/api/vehicles/DecodeVinValues/{vin}"
           f"?format=json")
    with urllib.request.urlopen(url, timeout=45) as r:
        rec = json.loads(r.read())["Results"][0]
    return rec


def main() -> None:
    vin16 = sys.argv[1] if len(sys.argv) > 1 else "3HAMMAALCL555829"
    res = repair_missing_check_digit(vin16)
    print(f"entrada        : {res['input']}  ({len(res['input'])} caracteres)")
    if not res["ok"]:
        print(f"NO reparable   : {res['reason']}")
        return
    print(f"digito calculado: posicion 9 = {res['vin'][8]}")
    print(f"VIN candidato  : {res['vin']}")
    print(f"año por codigo : {res['model_year']} (posicion 10 = {res['vin'][9]})")
    print(f"valida el propio checksum: {is_valid(res['vin'])}")
    print()
    rec = decode(res["vin"])
    err = (rec.get("ErrorText") or "").strip()
    print("NHTSA vPIC:")
    for k in ("ModelYear", "Make", "Model", "Series", "BodyClass", "VehicleType",
              "GVWR", "Manufacturer", "PlantCity", "PlantCountry"):
        if rec.get(k):
            print(f"  {k:14s} {rec[k]}")
    print(f"  {'ErrorText':14s} {err[:160]}")


if __name__ == "__main__":
    main()
