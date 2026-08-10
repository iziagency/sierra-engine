"""A policy term that already ended must say so out loud.

Found on SUMMI1's real file, 8.1.26. Its dossier carried a Progressive term
expiring 5.16.25; the loss run JC forwarded showed a NEWER Crum & Forster term,
5/17/2025 - 5/17/2026. Both are in the past. What happened:

  * gospel promotion declined the C&F date — correctly, you do not write an
    expired date into "current policy expires";
  * so the app went out carrying 5.16.25, the OLDER of two dead dates;
  * and the 60-day clock reported the loss run as "27 days before expiration,
    inside the 60-day window", which reads like everything is fine.

Nothing anywhere said the obvious thing: the newest coverage anyone can see
ended two and a half months ago. For a book that is mostly renewals, that is
the single most important fact in the file.
"""
from __future__ import annotations

import datetime

import lossruns


def run(carrier, eff, exp, valuation=None):
    return {"carrier": carrier, "effective_date": eff, "expiration_date": exp,
            "valuation_date": valuation}


def test_an_expired_newest_term_is_reported(monkeypatch):
    dossier = {"company": {"current_auto_expires": "5.16.25",
                           "current_auto_carrier": "Progressive Commercial"}}
    _, changes = lossruns.apply_gospel(
        dossier, [run("Crum & Forster", "5/17/2025", "5/17/2026")])
    joined = " ".join(changes)
    assert "expired" in joined.lower()
    assert "5.17.26" in joined                      # names the dead date
    assert "in force" in joined.lower()             # asks the real question


def test_the_expired_date_is_not_written_as_current():
    # The guard that already existed stays: a dead date never becomes the
    # answer to "when does the current policy expire".
    dossier = {"company": {"current_auto_expires": "5.16.25"}}
    lossruns.apply_gospel(dossier, [run("Crum & Forster", "5/17/2025", "5/17/2026")])
    assert dossier["company"]["current_auto_expires"] == "5.16.25"


def test_a_live_term_still_promotes_silently_as_before():
    future = datetime.date.today() + datetime.timedelta(days=200)
    dossier = {"company": {"current_auto_expires": "5.16.25"}}
    _, changes = lossruns.apply_gospel(
        dossier, [run("Crum & Forster", "5/17/2025", future.strftime("%m/%d/%Y"))])
    assert dossier["company"]["current_auto_expires"] == \
        f"{future.month}.{future.day}.{str(future.year)[2:]}"
    assert not any("expired" in c.lower() for c in changes)


def test_the_sixty_day_clock_does_not_call_a_dead_term_healthy():
    # "27 days before expiration, inside the 60-day window" about a term that
    # ended months ago reads as reassurance. It has to carry the caveat.
    notes = lossruns.sixty_day_clock(
        [run("Crum & Forster", "5/17/2025", "5/17/2026", "4/20/2026")],
        "5/17/2026")
    joined = " ".join(notes)
    assert "expired" in joined.lower()


def test_a_live_term_clock_note_is_unchanged():
    future = datetime.date.today() + datetime.timedelta(days=30)
    valuation = datetime.date.today() - datetime.timedelta(days=5)
    notes = lossruns.sixty_day_clock(
        [run("MSIG", "1/1/2026", future.strftime("%m/%d/%Y"),
             valuation.strftime("%m/%d/%Y"))],
        future.strftime("%m/%d/%Y"))
    joined = " ".join(notes)
    assert "inside the 60-day window" in joined
    assert "expired" not in joined.lower()
