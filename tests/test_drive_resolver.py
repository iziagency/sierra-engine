"""TDD for the multi-drive client-folder resolver in watcher/drive_api.py.

Context (see this change's final report for the full writeup): Claude/Clients/
Docs/Prospects are four SEPARATE shared drives, not folders inside one drive,
and the client is mid-migration from nested (legacy, by broker initials) to
flat folders. Both shapes exist in production right now, so the resolver must
handle both, and must never guess when an SP code resolves to more than one
folder.

Fixtures below reuse REAL names/ids pulled from the read-only inventory
(scratchpad/drive_inventory.json, captured 2026-07-27) rather than invented
ones:
  - LAKES Lakeside Towing: flat, at the Prospects drive root.
  - F&FTO F & F Towing Service: nested under "Prospects CG" (legacy,
    broker-initials container).
  - PARKW4 County Tow Service LLC: flat, at the Clients drive root.
  - HAMIL Hartley Towing: nested under "Clients AB".
  - ALPHA2 Alpha Towing & Recovery: a REAL duplicate - it exists under
    "Clients CG", "Clients inactive" AND "Clients MP" simultaneously.
  - FALCO1: a REAL cross-drive collision - "FALCO1 Falcon Ridge Towing LLC"
    already sits in the Claude sandbox (created by the bug this change fixes)
    while "FALCO1 Desert Valley Towing" (a different DBA!) sits in Prospects.
"""
from __future__ import annotations

import pytest

from drive_api import (
    CLAUDE_DRIVE_ID,
    CLIENTS_DRIVE_ID,
    DOCS_ROOT,
    PROSPECTS_DRIVE_ID,
    AmbiguousClientFolderError,
    CLIENTS_ROOT,
    DEFAULT_SEARCH_ROOTS,
    DriveRoot,
    PROSPECTS_ROOT,
    DriveWriteBlocked,
    assert_writable,
    find_candidates,
    list_all_identity_folders,
    parse_client_folder_name,
    resolve_client_folder,
    sandbox_mirror_root,
)
from fakes import FakeDriveGateway


def _prospects_with_lakes_and_fandf() -> FakeDriveGateway:
    gw = FakeDriveGateway()
    gw.folder("1VXjWZq9-2ZR_1L8b3J2CQ810hpJQhdUZ", "LAKES Lakeside Towing",
              PROSPECTS_DRIVE_ID, PROSPECTS_DRIVE_ID)
    gw.folder("1OU9HXtQv1n807HQJ08TImVS70LOcAB6i", "Prospects CG",
              PROSPECTS_DRIVE_ID, PROSPECTS_DRIVE_ID)
    gw.folder("1ZYRxnovlLLmq04wxaUCwqnIFpeExvUlI", "F&FTO F & F Towing Service",
              "1OU9HXtQv1n807HQJ08TImVS70LOcAB6i", PROSPECTS_DRIVE_ID)
    # A real file whose name happens to start with the LAKES token - must never
    # be mistaken for a folder candidate.
    gw.file("1decoy", "LAKES Google 7050 Perris Hill Rd report 7.15.24.pdf",
            PROSPECTS_DRIVE_ID, PROSPECTS_DRIVE_ID)
    return gw


class TestFlatMatch:
    def test_finds_lakes_in_the_flat_root(self):
        gw = _prospects_with_lakes_and_fandf()
        result = resolve_client_folder(gw, "LAKES", [PROSPECTS_ROOT])
        assert result.found and not result.is_ambiguous
        match = result.unique()
        assert match.id == "1VXjWZq9-2ZR_1L8b3J2CQ810hpJQhdUZ"
        assert match.name == "LAKES Lakeside Towing"
        assert match.nested is False
        assert match.path == "LAKES Lakeside Towing"

    def test_a_file_with_a_matching_name_prefix_is_not_a_candidate(self):
        # "LAKES Google ... report.pdf" exists for real (drive_inventory.json)
        # right alongside the folder - only folders may ever be matches.
        gw = _prospects_with_lakes_and_fandf()
        result = resolve_client_folder(gw, "LAKES", [PROSPECTS_ROOT])
        assert len(result.matches) == 1  # not 2


class TestNestedMatch:
    def test_finds_fandfto_nested_under_a_legacy_broker_container(self):
        gw = _prospects_with_lakes_and_fandf()
        result = resolve_client_folder(gw, "F&FTO", [PROSPECTS_ROOT])
        assert result.found and not result.is_ambiguous
        match = result.unique()
        assert match.id == "1ZYRxnovlLLmq04wxaUCwqnIFpeExvUlI"
        assert match.nested is True
        assert match.path == "Prospects CG/F&FTO F & F Towing Service"


class TestNoMatch:
    def test_unknown_sp_code_returns_empty_result_not_an_error(self):
        gw = _prospects_with_lakes_and_fandf()
        result = resolve_client_folder(gw, "ZZZZZ", [PROSPECTS_ROOT])
        assert result.matches == ()
        assert not result.found
        assert not result.is_ambiguous
        assert result.unique() is None


class TestAmbiguityWithinOneDrive:
    """ALPHA2 Alpha Towing & Recovery: verified in drive_inventory.json under
    three different broker containers of the SAME Clients drive at once."""

    def _clients_with_alpha2_x3(self) -> FakeDriveGateway:
        gw = FakeDriveGateway()
        containers = {"Clients CG": "c-cg", "Clients inactive": "c-inactive", "Clients MP": "c-mp"}
        for name, cid in containers.items():
            gw.folder(cid, name, CLIENTS_DRIVE_ID, CLIENTS_DRIVE_ID)
        for i, cid in enumerate(containers.values()):
            gw.folder(f"alpha2-{i}", "ALPHA2 Alpha Towing & Recovery", cid, CLIENTS_DRIVE_ID)
        return gw

    def test_all_three_locations_come_back_as_candidates(self):
        gw = self._clients_with_alpha2_x3()
        result = resolve_client_folder(gw, "ALPHA2", [CLIENTS_ROOT])
        assert result.is_ambiguous
        assert len(result.matches) == 3
        paths = {m.path for m in result.matches}
        assert paths == {
            "Clients CG/ALPHA2 Alpha Towing & Recovery",
            "Clients inactive/ALPHA2 Alpha Towing & Recovery",
            "Clients MP/ALPHA2 Alpha Towing & Recovery",
        }

    def test_unique_raises_instead_of_picking_one(self):
        gw = self._clients_with_alpha2_x3()
        result = resolve_client_folder(gw, "ALPHA2", [CLIENTS_ROOT])
        with pytest.raises(AmbiguousClientFolderError) as exc_info:
            result.unique()
        err = exc_info.value
        assert err.sp_code == "ALPHA2"
        assert len(err.matches) == 3
        # message must be informative enough for a broker-facing warning
        assert "ALPHA2" in str(err)
        assert "3" in str(err)


class TestAmbiguityAcrossDrives:
    """FALCO1: FALCO1 - Falcon Ridge Towing LLC already exists in the Claude
    sandbox (created by the bug this change fixes) while a DIFFERENT DBA,
    FALCO1 Desert Valley Towing, sits in Prospects open/ - a real SP-code
    collision the JC-knowledge docs flag as an undocumented case."""

    def test_same_code_in_two_configured_roots_is_ambiguous(self):
        gw = FakeDriveGateway()
        gw.folder("claude-clients", "Clients", CLAUDE_DRIVE_ID, CLAUDE_DRIVE_ID)
        gw.folder("claude-falco1", "FALCO1 - Falcon Ridge Towing LLC",
                  "claude-clients", CLAUDE_DRIVE_ID)
        gw.folder("prospects-open", "Prospects open", PROSPECTS_DRIVE_ID, PROSPECTS_DRIVE_ID)
        gw.folder("prospects-falco1", "FALCO1 Desert Valley Towing",
                  "prospects-open", PROSPECTS_DRIVE_ID)

        claude_clients_root = DriveRoot("Clients", CLAUDE_DRIVE_ID, "claude-clients")
        result = resolve_client_folder(gw, "FALCO1", [claude_clients_root, PROSPECTS_ROOT])
        assert result.is_ambiguous
        assert len(result.matches) == 2
        drive_ids = {m.drive_id for m in result.matches}
        assert drive_ids == {CLAUDE_DRIVE_ID, PROSPECTS_DRIVE_ID}


class TestDefaultSearchRoots:
    def test_default_roots_are_prospects_then_clients(self):
        assert DEFAULT_SEARCH_ROOTS == (PROSPECTS_ROOT, CLIENTS_ROOT)

    def test_docs_is_not_in_the_default_search_roots(self):
        # Docs only ever holds Apps/Bind packets/Knowledge base/OnPoint info
        # (verified against the live inventory) - never client folders.
        assert DOCS_ROOT not in DEFAULT_SEARCH_ROOTS


class TestListAllIdentityFolders:
    def test_returns_both_flat_and_nested_entries_unfiltered(self):
        gw = FakeDriveGateway()
        gw.folder("parkw4", "PARKW4 County Tow Service LLC", CLIENTS_DRIVE_ID, CLIENTS_DRIVE_ID)
        gw.folder("clients-ab", "Clients AB", CLIENTS_DRIVE_ID, CLIENTS_DRIVE_ID)
        gw.folder("hamil", "HAMIL Hartley Towing", "clients-ab", CLIENTS_DRIVE_ID)

        hits = list_all_identity_folders(gw, [CLIENTS_ROOT])
        names = {m.name for m in hits}
        assert names == {"PARKW4 County Tow Service LLC", "HAMIL Hartley Towing"}
        nested_by_name = {m.name: m.nested for m in hits}
        assert nested_by_name["PARKW4 County Tow Service LLC"] is False
        assert nested_by_name["HAMIL Hartley Towing"] is True


class TestSandboxMirrorRoot:
    """mirror_to_sandbox.py copies production into
    Claude/_TEST COPIES - not client deliverables/<Prospects|Clients|Docs>/...
    so sandbox tests/dry runs can resolve against a faithful copy without ever
    touching the real production drives."""

    def test_resolves_into_the_mirrored_subtree(self):
        gw = FakeDriveGateway()
        mirror = gw.folder("mirror", "_TEST COPIES - not client deliverables",
                            CLAUDE_DRIVE_ID, CLAUDE_DRIVE_ID)
        mirror_prospects = gw.folder("mirror-prospects", "Prospects", mirror.id, CLAUDE_DRIVE_ID)
        gw.folder("mirror-lakes", "LAKES Lakeside Towing", mirror_prospects.id, CLAUDE_DRIVE_ID)

        root = sandbox_mirror_root(gw, "Prospects")
        assert root == DriveRoot("Prospects", CLAUDE_DRIVE_ID, mirror_prospects.id)

        result = resolve_client_folder(gw, "LAKES", [root])
        assert result.unique().id == "mirror-lakes"

    def test_raises_lookup_error_when_the_mirror_does_not_exist(self):
        gw = FakeDriveGateway()  # empty Claude drive
        with pytest.raises(LookupError):
            sandbox_mirror_root(gw, "Prospects")


class TestParseClientFolderName:
    def test_real_convention_space_no_dash(self):
        assert parse_client_folder_name("LAKES Lakeside Towing") == ("LAKES", "Lakeside Towing")

    def test_legacy_dash_convention_still_parses(self):
        # created by the bug this change fixes - already exists in the sandbox,
        # must not be orphaned.
        assert parse_client_folder_name("BROOK1 - Brookfield Towing, LLC") == (
            "BROOK1", "Brookfield Towing, LLC")

    def test_sp_code_with_no_trailing_name_is_a_blank_dba_not_a_crash(self):
        # real data: 'Prospects open/GUSTA1' has no business name at all.
        assert parse_client_folder_name("GUSTA1") == ("GUSTA1", "")

    def test_ampersand_codes_survive(self):
        assert parse_client_folder_name("F&FTO F & F Towing Service") == (
            "F&FTO", "F & F Towing Service")


class TestWriteGuard:
    def test_claude_drive_is_writable(self):
        assert_writable(CLAUDE_DRIVE_ID)  # must not raise

    def test_any_other_drive_is_refused(self):
        for drive_id in (CLIENTS_DRIVE_ID, PROSPECTS_DRIVE_ID, "some-typo-id"):
            with pytest.raises(DriveWriteBlocked):
                assert_writable(drive_id)

    def test_fake_gateway_create_folder_enforces_the_same_guard(self):
        gw = FakeDriveGateway()
        with pytest.raises(DriveWriteBlocked):
            gw.create_folder("New Client", "some-parent", CLIENTS_DRIVE_ID)
        assert gw.count() == 0  # nothing was created
