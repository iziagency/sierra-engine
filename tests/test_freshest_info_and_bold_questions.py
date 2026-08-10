"""Two rules for a file built out of Drive.

**Freshest information wins.** A folder's newest CAP packet is not necessarily
its newest FACT: CARRS carries a loss run dated 8.3.26 next to paperwork from
7.30, and Sierra's own doctrine is that a loss run is gospel. Reading only the
packet would build an app that was already out of date the moment it was made.
So anything dated after the packet is read on top of it.

**An unsure answer asks, in bold.** A question buried in a paragraph gets
skimmed past; the broker has to see WHAT is being asked at a glance. Every
question names its subject in bold, and the subject is the thing they have to
go find out.
"""
from __future__ import annotations

import drive_pull as dp
import slack_engine as se


# ------------------------------------------------------- freshest information

def test_material_newer_than_the_packet_is_read_on_top_of_it():
    got = dp.classify(["SOUTH5 CAP tow QP 7.30.26.pdf",
                       "SOUTH5 CAP MSIG LR 8.2.26.pdf"])
    assert got["mode"] == "packet"
    assert got["newer"] == ["SOUTH5 CAP MSIG LR 8.2.26.pdf"]


def test_material_older_than_the_packet_is_left_alone():
    # The packet already contains it; re-reading costs a model call for nothing.
    got = dp.classify(["SOUTH5 CAP tow QP 7.30.26.pdf",
                       "SOUTH5 CAP MSIG LR 6.1.26.pdf"])
    assert got["newer"] == []


def test_several_newer_pieces_all_come_along_newest_first():
    got = dp.classify(["X CAP tow QP 7.1.26.pdf", "X CAP LR 8.3.26.pdf",
                       "X CAP COI 7.15.26.pdf"])
    assert got["newer"] == ["X CAP LR 8.3.26.pdf", "X CAP COI 7.15.26.pdf"]


def test_an_undated_file_is_not_assumed_newer():
    got = dp.classify(["X CAP tow QP 7.1.26.pdf", "random photo.png"])
    assert got["newer"] == []


def test_a_folder_with_only_a_packet_has_nothing_newer():
    assert dp.classify(["ONSIG2 CAP auh QP 8.3.26.pdf"])["newer"] == []


# ------------------------------------------------------------ bold questions

def test_a_question_names_its_subject_in_bold():
    out = se.ask("current bodily injury liability limit",
                 "nothing on file; the quote is priced off it")
    assert out.startswith(":grey_question: *current bodily injury liability limit*")
    assert "priced off it" in out


def test_the_reply_bolds_every_hole_it_reports():
    out = se.format_reply({
        "ok": True, "sp_name": "HAMIL", "client": "Hartley Towing",
        "headline": "app filled", "filled_summary": "1 vehicle",
        "red_flags": [], "missing": [],
        "rts": {"applies": True, "delivered": "x.xlsx", "cells": 56,
                "reason": "California tow risk, 1 power unit.",
                "unknown_blanks": ["current bodily injury liability limit — "
                                   "nothing on file; the quote is priced off it"]},
    })
    assert "*current bodily injury liability limit*" in out


def test_a_hole_with_no_dash_still_bolds_the_whole_subject():
    assert se.ask("USDOT number", "") == ":grey_question: *USDOT number*"


def test_bolding_never_doubles_up_on_text_already_bold():
    assert se.ask("*already bold*", "why").count("*already bold*") == 1
