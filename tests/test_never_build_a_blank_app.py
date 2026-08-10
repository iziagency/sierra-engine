"""A blank application is worse than an error: it looks like work.

Live on 8.3. `Prep ONSIG2 CAP app` could not read their flattened QP, so
adoption failed — but the empty shell it had just created stayed on disk:

    wayfield-towing-services-ab/state.json   ->   {"sp_code": "ONSIG2"}

The next attempt found that file, decided the client was known, never went
back to Drive, and printed a Sierra Pacific application with nothing in it.
Every field blank, a Drive link, a green check.

Two rules. A failed adoption leaves nothing behind, so the next try starts
clean. And no application is ever built from a dossier that does not even name
the insured.
"""
from __future__ import annotations

import json

import slack_engine as se


def shell(tmp_path, slug, **extra):
    d = tmp_path / slug
    d.mkdir(parents=True, exist_ok=True)
    (d / "state.json").write_text(json.dumps({"sp_code": "ONSIG2", **extra}),
                                  encoding="utf-8")
    return d


# ------------------------------------------------- nothing to build an app from

def test_a_dossier_with_only_a_code_has_nothing_to_print(tmp_path):
    assert se.has_enough_to_build({"sp_code": "ONSIG2"}) is False


def test_a_dossier_with_no_company_block_has_nothing(tmp_path):
    assert se.has_enough_to_build({"sp_code": "ONSIG2", "company": None}) is False


def test_a_named_insured_is_the_minimum(tmp_path):
    assert se.has_enough_to_build(
        {"sp_code": "ONSIG2",
         "company": {"first_named_insured": "On Sight Towing LLC"}}) is True


def test_a_dba_alone_is_enough_to_name_the_risk(tmp_path):
    assert se.has_enough_to_build(
        {"sp_code": "X", "company": {"dba": "Apex Auto Transport"}}) is True


def test_an_empty_company_block_is_not_enough(tmp_path):
    assert se.has_enough_to_build({"sp_code": "X", "company": {}}) is False


def test_vehicles_without_a_name_are_still_not_an_application(tmp_path):
    # Half a file is a hole to report, not a document to print.
    assert se.has_enough_to_build(
        {"sp_code": "X", "company": {}, "vehicles": [{"vin": "1" * 17}]}) is False


# ---------------------------------------------- a failed adoption leaves nothing

def test_a_failed_adoption_removes_the_shell_it_made(tmp_path):
    d = shell(tmp_path, "wayfield-towing-services-ab")
    se.discard_empty_shell(d)
    assert not d.exists()


def test_a_dossier_with_real_data_is_never_discarded(tmp_path):
    d = shell(tmp_path, "real-client",
              company={"first_named_insured": "Real Towing LLC"})
    se.discard_empty_shell(d)
    assert d.exists()


def test_discarding_a_folder_that_is_already_gone_is_fine(tmp_path):
    se.discard_empty_shell(tmp_path / "never-existed")


def test_a_folder_holding_other_work_is_left_alone(tmp_path):
    # Only the shell WE just created may be removed — never a folder that has
    # accumulated anything else.
    d = shell(tmp_path, "has-history")
    (d / "CHANGELOG.md").write_text("something", encoding="utf-8")
    se.discard_empty_shell(d)
    assert d.exists()
