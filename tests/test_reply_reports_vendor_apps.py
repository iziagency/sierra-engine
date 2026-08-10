"""The Slack reply says which of the two phase-1 documents came out.

The reply is deliberately four lines — a broker between calls will not read
fourteen. But "the RTS wasn't made" cannot be one of the things left implicit,
because the broker's next move depends on it: chase the missing state, or move
on with the SP app alone.
"""
from __future__ import annotations

import slack_engine as se

BASE = {
    "ok": True, "sp_name": "LAKES", "client": "Lakeside Towing LLC",
    "headline": "app filled", "filled_summary": "1 vehicle · 1 driver",
    "folder_link": "https://drive.google.com/x", "red_flags": [], "missing": [],
}


def test_a_built_rts_is_announced_with_its_cell_count():
    out = se.format_reply(dict(BASE, rts={
        "applies": True, "delivered": "LAKES CAP RTS supp app 8.1.26.xlsx",
        "cells": 54, "reason": "California tow risk, 1 power unit."}))
    assert "RTS/Progressive app filled — 54 cells." in out


def test_a_hole_in_the_rts_is_reported_a_deliberate_blank_is_not():
    # JC's three-states rule: "was that on purpose or did you not know?" has to
    # be answerable from the message. The purposeful blanks (axles, loan/lease)
    # never make noise; the genuine unknowns always do.
    out = se.format_reply(dict(BASE, rts={
        "applies": True, "delivered": "LAKES CAP RTS supp app 8.1.26.xlsx",
        "cells": 54, "reason": "California tow risk, 2 power units.",
        "unknown_blanks": ["comp/collision for vehicle 2 — no stated value on file"]}))
    assert "comp/collision for vehicle 2" in out
    assert "axles" not in out.lower()


def test_an_out_of_state_risk_is_told_it_gets_one_document():
    out = se.format_reply(dict(BASE, rts={
        "applies": False, "unknown": False,
        "reason": "TX, not California — Sierra Pacific app only."}))
    assert "SP app only — TX, not California" in out


def test_an_undecidable_route_reads_as_a_question_not_a_result():
    out = se.format_reply(dict(BASE, rts={
        "applies": False, "unknown": True,
        "reason": "No state on file, so the RTS routing rule can't be applied "
                  "— where is this risk located?"}))
    assert "RTS undecided" in out
    assert "where is this risk located?" in out


def test_a_failed_build_is_never_silent():
    out = se.format_reply(dict(BASE, rts={
        "applies": True, "reason": "California tow risk, 1 power unit.",
        "error": "RuntimeError: template missing"}))
    assert "RTS app failed" in out
    assert "template missing" in out


def test_an_older_result_without_the_field_still_renders():
    # process_drop results predating this change carry no 'rts' key at all.
    out = se.format_reply(dict(BASE))
    assert "RTS" not in out
    assert "Lakeside Towing LLC" in out
