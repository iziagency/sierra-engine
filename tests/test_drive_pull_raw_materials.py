"""Some clients have a packet to read; others have a pile of photographs.

Two real folders, same day:

    ONSIG2 Onsight Towing Services - AB
        ONSIG2 CAP auh QP 8.3.26.pdf              <- read the form, no model

    CARRS Carrs Towing
        CARRS CAP 2023-26 LR 8.3.26               <- loss run
        CARRS CAP veh info 7.30.26                <- vehicle schedule
        IMG_9499.PNG, IMG_9461.jpeg, ...heic      <- photos of paperwork
        Screenshot 2026-07-30 at 3.50.13 PM.png

CARRS has no QP and no app: the app has to be BUILT from that material, which
is the ordinary extraction path pointed at Drive instead of Slack. Choosing
between the two modes is what this covers — reading a photo when a filled form
was sitting there is waste, and declaring "nothing to read" when the folder is
full of the client's paperwork is worse.
"""
from __future__ import annotations

import drive_pull as dp

CARRS = ["CARRS CAP 2023-26 LR 8.3.26.pdf", "IMG_9499.PNG",
         "Screenshot 2026-07-30 at 3.50.13 PM.png", "IMG_9461.jpeg",
         "CARRS CAP veh info 7.30.26.pdf",
         "80706552027__676E7467-61C8-4042.heic"]


def test_a_folder_with_a_packet_reads_the_packet():
    got = dp.classify(["ONSIG2 CAP auh QP 8.3.26.pdf", "IMG_1.png"])
    assert got["mode"] == "packet"
    assert got["files"] == ["ONSIG2 CAP auh QP 8.3.26.pdf"]


def test_a_folder_without_one_hands_over_its_materials():
    got = dp.classify(CARRS)
    assert got["mode"] == "materials"
    assert "CARRS CAP 2023-26 LR 8.3.26.pdf" in got["files"]
    assert "IMG_9499.PNG" in got["files"]


def test_heic_photos_count_brokers_shoot_on_iphones():
    assert "80706552027__676E7467-61C8-4042.heic" in dp.classify(CARRS)["files"]


def test_our_own_bookkeeping_is_never_client_material():
    got = dp.classify(["Change History.pdf", "CHANGELOG.md", "IMG_1.png"])
    assert got["files"] == ["IMG_1.png"]


def test_a_folder_with_nothing_readable_says_so():
    got = dp.classify(["notes.txt", "Change History.pdf"])
    assert got["mode"] == "empty"
    assert got["files"] == []


def test_materials_come_back_newest_first():
    # A drop is read in order and later material should not be buried behind
    # last year's; the dated names carry the order.
    got = dp.classify(["X CAP veh info 7.30.26.pdf", "X CAP LR 8.3.26.pdf"])
    assert got["files"][0] == "X CAP LR 8.3.26.pdf"


def test_the_pile_is_capped_and_the_cap_is_reported():
    many = [f"IMG_{i:04d}.png" for i in range(1, 40)]
    got = dp.classify(many)
    assert len(got["files"]) == dp.MAX_MATERIALS
    assert got["dropped"] == len(many) - dp.MAX_MATERIALS


def test_a_normal_pile_reports_nothing_dropped():
    assert dp.classify(CARRS)["dropped"] == 0
