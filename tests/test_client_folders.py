"""TDD for watcher/drive_api.py::client_folders() and ensure_folder() after the
multi-drive fix.

client_folders() keeps its exact pre-existing signature and (clients_id,
client_id, source_id) return shape - process_drop.py, qp_build.py and
slack_engine.py all call it positionally as client_folders(sp, name) - but its
internals now go through the shared resolver instead of the old
"name contains '<sp> - '" query, and new folders are created with the real
"<SP> <Name>" (space, no dash) convention instead of the wrong dash one.

Every test injects a FakeDriveGateway (see tests/fakes.py) via the new
`gateway=` parameter, so nothing here ever calls service()/googleapiclient or
touches the network - the `svc` parameter is left at its default (unused
whenever a gateway is supplied, since `gateway or GoogleDriveGateway(svc or
service())` short-circuits before service() would ever run).
"""
from __future__ import annotations

import pytest

import drive_api
from drive_api import (
    CLAUDE_DRIVE_ID,
    CLIENTS_DRIVE_ID,
    AmbiguousClientFolderError,
    DriveWriteBlocked,
    client_folders,
)
from fakes import FakeDriveGateway


class TestCreatesNewFolder:
    def test_new_client_uses_the_real_space_convention(self):
        gw = FakeDriveGateway()
        clients_id, client_id, source_id = client_folders("LAKES", "Lakeside Towing", gateway=gw)

        created = gw.list_children(clients_id, CLAUDE_DRIVE_ID)
        assert [c.name for c in created if c.id == client_id] == ["LAKES Lakeside Towing"]
        # never the legacy dash form
        assert not any(c.name.startswith("LAKES - ") for c in created)

    def test_source_subfolder_is_created_under_the_new_client_folder(self):
        gw = FakeDriveGateway()
        _, client_id, source_id = client_folders("LAKES", "Lakeside Towing", gateway=gw)
        children = gw.list_children(client_id, CLAUDE_DRIVE_ID)
        assert any(c.id == source_id and c.name == "_source" for c in children)

    def test_new_folder_is_created_inside_the_claude_drive(self):
        gw = FakeDriveGateway()
        clients_id, client_id, _ = client_folders("LAKES", "Lakeside Towing", gateway=gw)
        match = next(c for c in gw.list_children(clients_id, CLAUDE_DRIVE_ID) if c.id == client_id)
        assert match.drive_id == CLAUDE_DRIVE_ID


class TestReusesExistingFolder:
    def test_reuses_a_folder_already_in_the_real_convention(self):
        gw = FakeDriveGateway()
        clients = gw.folder("clients", "Clients", CLAUDE_DRIVE_ID, CLAUDE_DRIVE_ID)
        existing = gw.folder("existing-santo", "LAKES Lakeside Towing", clients.id, CLAUDE_DRIVE_ID)

        _, client_id, _ = client_folders("LAKES", "Lakeside Towing", gateway=gw)

        assert client_id == existing.id
        # no duplicate created
        assert len([c for c in gw.list_children(clients.id, CLAUDE_DRIVE_ID)
                    if c.name.split(" ", 1)[0] == "LAKES"]) == 1

    def test_reuses_an_existing_legacy_dash_named_folder_without_orphaning_it(self):
        # This exact folder already exists in the real Claude sandbox today -
        # created by the bug this change fixes (see drive_inventory.json).
        gw = FakeDriveGateway()
        clients = gw.folder("clients", "Clients", CLAUDE_DRIVE_ID, CLAUDE_DRIVE_ID)
        existing = gw.folder("existing-brook1", "BROOK1 - Brookfield Towing, LLC",
                              clients.id, CLAUDE_DRIVE_ID)

        _, client_id, _ = client_folders("BROOK1", "Brookfield Towing, LLC", gateway=gw)

        assert client_id == existing.id
        assert gw.count() == 3  # clients + existing + _source only - no new client folder


class TestAmbiguousSandboxFolders:
    def test_raises_instead_of_guessing_and_creates_nothing(self):
        gw = FakeDriveGateway()
        clients = gw.folder("clients", "Clients", CLAUDE_DRIVE_ID, CLAUDE_DRIVE_ID)
        gw.folder("dup-1", "DUPLI Duplicate Towing A", clients.id, CLAUDE_DRIVE_ID)
        gw.folder("dup-2", "DUPLI - Duplicate Towing B", clients.id, CLAUDE_DRIVE_ID)
        before = gw.count()

        with pytest.raises(AmbiguousClientFolderError) as exc_info:
            client_folders("DUPLI", "Duplicate Towing", gateway=gw)

        assert exc_info.value.sp_code == "DUPLI"
        assert len(exc_info.value.matches) == 2
        assert gw.count() == before  # nothing new was created while conflicted


class TestWriteTargetConfiguration:
    def test_default_write_target_is_the_claude_sandbox(self):
        assert drive_api.WRITE_TARGET_DRIVE_ID == CLAUDE_DRIVE_ID

    def test_repointing_the_write_target_off_claude_is_refused(self, monkeypatch):
        # Defense in depth: assert_writable() is pinned to the literal Claude
        # drive id, independent of this constant, so even a deliberate
        # repoint is refused rather than silently writing into a production
        # drive. Loosening that assertion is a separate, reviewed change.
        monkeypatch.setattr(drive_api, "WRITE_TARGET_DRIVE_ID", CLIENTS_DRIVE_ID)
        gw = FakeDriveGateway()
        with pytest.raises(DriveWriteBlocked):
            client_folders("LAKES", "Lakeside Towing", gateway=gw)
