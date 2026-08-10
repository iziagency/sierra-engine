"""TDD for watcher/client_match.py after the multi-drive Drive fix.

drive_client_folders() used to call ensure_folder("Clients", svc=svc) - which
creates/searches INSIDE the Claude sandbox drive - and only ever looked at
DIRECT children, missing virtually every real client (almost all of the
production Clients drive's ~15k folders are nested one level under a
broker-initials container; see drive_inventory.json). It now reads the real,
read-only, production Clients drive through the shared resolver and returns
both flat and nested names.

build_index()'s drive-folder loop assumed every Drive name contained
" - " (`folder.split(" - ", 1)[-1]`), which silently broke on the real
"<SP> <Name>" (space, no dash) convention - the split is a no-op on a string
with no " - ", so `dba` came out as the WHOLE name, SP code included. It now
goes through drive_api.parse_client_folder_name(), which handles both.
"""
from __future__ import annotations

import json

import client_match
from drive_api import CLIENTS_DRIVE_ID, PROSPECTS_DRIVE_ID
from fakes import FakeDriveGateway


class TestDriveClientFolders:
    def test_returns_flat_and_nested_names_from_the_clients_drive(self):
        gw = FakeDriveGateway()
        gw.folder("parkw4", "PARKW4 County Tow Service LLC", CLIENTS_DRIVE_ID, CLIENTS_DRIVE_ID)
        ab = gw.folder("clients-ab", "Clients AB", CLIENTS_DRIVE_ID, CLIENTS_DRIVE_ID)
        gw.folder("hamil", "HAMIL Hartley Towing", ab.id, CLIENTS_DRIVE_ID)

        names = client_match.drive_client_folders(gateway=gw)

        assert "PARKW4 County Tow Service LLC" in names
        assert "HAMIL Hartley Towing" in names
        # the container itself is not a client
        assert "Clients AB" not in names

    def test_does_not_search_the_prospects_drive(self):
        # drive_client_folders' docstring/intent is specifically the Clients/
        # folders - a live prospect is not yet a client.
        gw = FakeDriveGateway()
        gw.folder("only-a-prospect", "LAKES Lakeside Towing",
                  PROSPECTS_DRIVE_ID, PROSPECTS_DRIVE_ID)
        names = client_match.drive_client_folders(gateway=gw)
        assert names == []

    def test_returns_empty_list_when_the_gateway_raises(self):
        # best-effort contract preserved: matching still works on local
        # dossiers alone if Drive is unreachable.
        class BrokenGateway:
            def list_children(self, parent_id, drive_id):
                raise RuntimeError("simulated Drive outage")

        assert client_match.drive_client_folders(gateway=BrokenGateway()) == []


class TestBuildIndexDriveNameParsing:
    def test_real_space_convention_dba_excludes_the_sp_code(self, monkeypatch, tmp_path):
        monkeypatch.setattr(client_match, "CLIENTS", tmp_path)  # no local dossiers
        index = client_match.build_index(["LAKES Lakeside Towing"])
        assert len(index) == 1
        # the SP code must never leak into the parsed display name
        assert index[0]["label"] == "Lakeside Towing"
        assert client_match.norm_name("Lakeside Towing") in index[0]["names"]

    def test_legacy_dash_convention_still_parses(self, monkeypatch, tmp_path):
        monkeypatch.setattr(client_match, "CLIENTS", tmp_path)
        index = client_match.build_index(["BROOK1 - Brookfield Towing, LLC"])
        assert index[0]["label"] == "Brookfield Towing, LLC"

    def test_sp_code_with_no_business_name_is_skipped_not_indexed_blank(self, monkeypatch, tmp_path):
        # real data: 'Prospects open/GUSTA1' has no DBA at all.
        monkeypatch.setattr(client_match, "CLIENTS", tmp_path)
        index = client_match.build_index(["GUSTA1"])
        assert index == []

    def test_still_dedupes_against_a_local_dossier_with_the_same_slug(self, monkeypatch, tmp_path):
        # Real folder "PARKW4 County Tow Service LLC" -> dba "County Tow
        # Service LLC" -> slug "county-tow-service-llc", the same slug a
        # local dossier folder would get from slugify() on that DBA - unlike
        # "LAKES Lakeside Towing", where the Drive DBA omits "LLC" and so
        # never lines up with an on-disk "...-llc" slug at all (a
        # pre-existing, orthogonal quirk of comparing raw slugs that this
        # change does not touch).
        client_dir = tmp_path / "county-tow-service-llc"
        client_dir.mkdir()
        (client_dir / "state.json").write_text(
            json.dumps({"company": {"first_named_insured": "County Tow Service LLC"}}),
            encoding="utf-8",
        )
        monkeypatch.setattr(client_match, "CLIENTS", tmp_path)

        index = client_match.build_index(["PARKW4 County Tow Service LLC"])

        # one entry, not two - the drive-derived duplicate must be dropped
        assert len(index) == 1
        assert index[0]["slug"] == "county-tow-service-llc"
        assert "drive_only" not in index[0]


class TestFindMatchStillWorksWithFixedNames:
    def test_fuzzy_match_finds_the_drive_only_entry(self, monkeypatch, tmp_path):
        monkeypatch.setattr(client_match, "CLIENTS", tmp_path)
        index = client_match.build_index(["LAKES Lakeside Towing"])
        slug, reason = client_match.find_match(
            {"company": {"first_named_insured": "Lakeside Towing LLC"}}, index)
        assert slug == index[0]["slug"]
        assert "name" in reason
