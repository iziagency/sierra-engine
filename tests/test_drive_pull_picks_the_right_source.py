"""Which paper in a client's Drive folder the engine should read.

A folder is not a pile: it holds this year's QP, last year's QP, loss runs,
COIs and the folders of policies already lost. Reading the wrong one produces
a submission built on stale facts, which looks exactly as finished as a good
one.

Real listing, SOUTH5 South West Towing:

    SOUTH5 CAP tow QP 7.30.26.pdf
    [CAP]  [MTC lost 2025]  [APD lost 2025]  [CAP lost 2025]  ...

So: the newest CAP QP wins; a CAP app is the fallback when no QP exists; and
anything filed under a "lost" folder is history, never a source.
"""
from __future__ import annotations

import drive_pull as dp


def test_the_newest_qp_wins():
    files = ["SOUTH5 CAP tow QP 6.10.26.pdf", "SOUTH5 CAP tow QP 7.30.26.pdf"]
    assert dp.pick_source(files) == "SOUTH5 CAP tow QP 7.30.26.pdf"


def test_a_completed_qp_outranks_a_plain_one_from_the_same_day():
    files = ["LAKES CAP tow QP 7.22.26.pdf", "LAKES CAP tow QP 7.22.26 comp.pdf"]
    assert dp.pick_source(files) == "LAKES CAP tow QP 7.22.26 comp.pdf"


def test_the_app_is_the_fallback_when_no_qp_exists():
    files = ["FALCO1 CAP app 7.29.26.pdf", "Change History.pdf"]
    assert dp.pick_source(files) == "FALCO1 CAP app 7.29.26.pdf"


def test_a_qp_beats_an_app_even_if_the_app_is_newer():
    # The QP carries the app's pages plus everything else.
    files = ["HAMIL CAP app 8.1.26.pdf", "HAMIL CAP tow QP 7.30.26.pdf"]
    assert dp.pick_source(files) == "HAMIL CAP tow QP 7.30.26.pdf"


def test_loss_runs_and_cois_are_never_the_source():
    files = ["LAKES CAP MSIG LR 2025 7.7.26.pdf", "LAKES CAP OB COI 2025-26 7.15.26.pdf"]
    assert dp.pick_source(files) is None


def test_a_workers_comp_or_garage_packet_is_not_a_CAP_source():
    assert dp.pick_source(["HAMIL WC tow QP 7.30.26.pdf"]) is None


def test_an_empty_folder_yields_nothing():
    assert dp.pick_source([]) is None
    assert dp.pick_source(["Change History.pdf", "notes.md"]) is None


def test_a_file_with_no_date_loses_to_one_that_has_it():
    files = ["SOUTH5 CAP tow QP.pdf", "SOUTH5 CAP tow QP 7.30.26.pdf"]
    assert dp.pick_source(files) == "SOUTH5 CAP tow QP 7.30.26.pdf"


def test_only_a_dated_name_is_needed_to_order_them():
    files = ["X CAP tow QP 1.5.27.pdf", "X CAP tow QP 12.20.26.pdf"]
    assert dp.pick_source(files) == "X CAP tow QP 1.5.27.pdf"


def test_a_lost_policy_folder_is_history_not_a_source():
    # Folders are passed in separately; this guards the name filter itself.
    assert dp.is_history("CAP lost 2024") is True
    assert dp.is_history("MTC lost 2025") is True
    assert dp.is_history("CAP") is False
    assert dp.is_history("CAP 2025 AUW") is False
