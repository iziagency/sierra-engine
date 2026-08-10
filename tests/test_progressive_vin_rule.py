"""Progressive wins a VIN mismatch — JC, 7.29 call, verbatim:

    "if we get a document for Progressive and there's a mismatch on VIN
     numbers, go with the Progressive one… that's more reliable."

Mechanics: vehicles are keyed by VIN, so a Progressive document carrying a
different VIN for the same physical truck does not conflict — it lands as a
second unit next to the first (exactly Lakeside's day-one finding: the app's
2016 Hino and the COI's 2018 Hino). The rule collapses that pair when the
source is Progressive: same year+make fingerprint, different VIN, the VIN
printed on the Progressive paper wins, and the correction is announced.

The rule needs BOTH conditions — a Progressive document in the drop AND the
disputed VIN actually printed on it. A Progressive dec page attached to a drop
does not make every other paper in the same drop gospel.
"""
from __future__ import annotations

import fitz

import process_drop as pd


def make_pdf(tmp_path, name: str, text: str) -> str:
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), text, fontsize=10)
    out = tmp_path / name
    doc.save(out)
    doc.close()
    return str(out)


PROG_TEXT = ("Progressive Commercial\nCommercial Auto Insurance Coverage Summary\n"
             "2016 HINO 268  VIN: 5PVNJ8JN1J4S53169\nPolicy 06588204-0")


# ------------------------------------------------------------- doc detection

def test_a_progressive_pdf_is_detected(tmp_path):
    f = make_pdf(tmp_path, "dec.pdf", PROG_TEXT)
    assert pd.progressive_vins([f]) == {"5PVNJ8JN1J4S53169"}


def test_a_non_progressive_pdf_yields_nothing(tmp_path):
    f = make_pdf(tmp_path, "coi.pdf",
                 "Century One Insurance\n2016 HINO VIN: 5PVNJ8JN1J4S53169")
    assert pd.progressive_vins([f]) == set()


def test_images_and_missing_files_never_crash(tmp_path):
    img = tmp_path / "photo.jpg"
    img.write_bytes(b"\xff\xd8\xff\xe0 not a pdf")
    assert pd.progressive_vins([str(img), str(tmp_path / "gone.pdf")]) == set()


# ------------------------------------------------------------ reconciliation

def dossier_with(*vehicles):
    return {"vehicles": [dict(v) for v in vehicles]}


FILE_TRUCK = {"year": 2016, "maker": "Hino", "vin": "5PVNJ8JN5G4S52079",
              "stated_value": 55000}
PROG_TRUCK = {"year": 2016, "maker": "Hino", "vin": "5PVNJ8JN1J4S53169"}


def test_progressive_vin_collapses_the_duplicate_pair():
    d = dossier_with(FILE_TRUCK, PROG_TRUCK)
    notes = pd.reconcile_progressive_vins(d, {"5PVNJ8JN1J4S53169"})
    assert len(d["vehicles"]) == 1
    v = d["vehicles"][0]
    assert v["vin"] == "5PVNJ8JN1J4S53169"          # Progressive's VIN wins
    assert v["stated_value"] == 55000               # the file's detail survives
    assert notes and "Progressive" in notes[0]


def test_without_a_progressive_doc_nothing_moves():
    d = dossier_with(FILE_TRUCK, PROG_TRUCK)
    notes = pd.reconcile_progressive_vins(d, set())
    assert len(d["vehicles"]) == 2
    assert notes == []


def test_a_vin_not_printed_on_the_progressive_paper_does_not_win():
    # The dec page names a different unit entirely; the pair stays visible.
    d = dossier_with(FILE_TRUCK, PROG_TRUCK)
    notes = pd.reconcile_progressive_vins(d, {"1FDUF5HT8PDA00001"})
    assert len(d["vehicles"]) == 2
    assert notes == []


def test_different_year_or_make_is_a_second_truck_not_a_mismatch():
    other = {"year": 2022, "maker": "Ford", "vin": "1FDUF5HT8PDA00001"}
    d = dossier_with(FILE_TRUCK, other)
    pd.reconcile_progressive_vins(d, {"1FDUF5HT8PDA00001"})
    assert len(d["vehicles"]) == 2


def test_matching_vins_need_no_reconciling():
    d = dossier_with(FILE_TRUCK)
    notes = pd.reconcile_progressive_vins(d, {"5PVNJ8JN5G4S52079"})
    assert len(d["vehicles"]) == 1
    assert notes == []


def test_both_vins_on_the_progressive_paper_is_two_insured_units():
    # Progressive itself lists both — that is a two-truck policy, not a
    # mismatch. Collapsing it would drop an insured unit.
    d = dossier_with(FILE_TRUCK, PROG_TRUCK)
    notes = pd.reconcile_progressive_vins(
        d, {"5PVNJ8JN5G4S52079", "5PVNJ8JN1J4S53169"})
    assert len(d["vehicles"]) == 2
    assert notes == []
