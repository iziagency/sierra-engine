"""Where a finished document gets filed.

JC's ask on the 7.27 call was "drop it back in that same folder" — the client's
own folder, alongside their loss runs and QP. Production drives are read-only in
code, so the faithful rehearsal is to file into the mirror of production that
lives inside the Claude sandbox: the output lands next to the very same files it
would sit beside in production, and nothing writes outside the sandbox.
"""
from __future__ import annotations

import pytest

import drive_api
from fakes import FakeDriveGateway


SANDBOX = drive_api.SANDBOX_MIRROR_PREFIX
CLAUDE = drive_api.CLAUDE_DRIVE_ID


def mirrored_gateway():
    """A Claude drive shaped like the real mirror: sandbox -> Prospects -> LAKES."""
    gw = FakeDriveGateway()
    mirror = gw.folder("m", SANDBOX, CLAUDE, CLAUDE)
    prospects = gw.folder("pros", "Prospects", mirror.id, CLAUDE)
    clients = gw.folder("cli", "Clients", mirror.id, CLAUDE)
    lakeside = gw.folder("santo", "LAKES Lakeside Towing", prospects.id, CLAUDE)
    return gw, prospects, clients, lakeside


def test_files_into_the_existing_client_folder_inside_the_mirror(monkeypatch):
    monkeypatch.setattr(drive_api, "FILING_MODE", "sandbox-mirror")
    gw, _prospects, _clients, lakeside = mirrored_gateway()
    _, client_id, source_id = drive_api.client_folders("LAKES", "Lakeside Towing", gateway=gw)
    assert client_id == lakeside.id, "must reuse Lakeside's mirrored folder, not make a new one"
    assert source_id  # _source created underneath it


def test_a_brand_new_client_lands_under_prospects(monkeypatch):
    monkeypatch.setattr(drive_api, "FILING_MODE", "sandbox-mirror")
    gw, prospects, _clients, _lakeside = mirrored_gateway()
    _, client_id, _ = drive_api.client_folders("NEWCO", "Newco Towing LLC", gateway=gw)
    created = gw.get(client_id)
    assert created.name == "NEWCO Newco Towing LLC", "real convention: space, no dash"
    assert created.parent_id == prospects.id, "new business is a prospect, not a client"


def test_an_existing_client_in_the_clients_mirror_is_found_too(monkeypatch):
    monkeypatch.setattr(drive_api, "FILING_MODE", "sandbox-mirror")
    gw, _prospects, clients, _lakeside = mirrored_gateway()
    county = gw.folder("cnt", "PARKW4 County Tow Service LLC", clients.id, CLAUDE)
    _, client_id, _ = drive_api.client_folders("PARKW4", "County Tow Service LLC", gateway=gw)
    assert client_id == county.id


def test_falls_back_when_the_mirror_has_not_been_created(monkeypatch):
    # A fresh machine that never ran mirror_to_sandbox.py must still work rather
    # than refusing to file anything.
    monkeypatch.setattr(drive_api, "FILING_MODE", "sandbox-mirror")
    gw = FakeDriveGateway()
    _, client_id, _ = drive_api.client_folders("LAKES", "Lakeside Towing", gateway=gw)
    assert gw.get(client_id).name == "LAKES Lakeside Towing"


def test_never_files_outside_the_claude_drive(monkeypatch):
    monkeypatch.setattr(drive_api, "FILING_MODE", "sandbox-mirror")
    gw, _p, _c, _s = mirrored_gateway()
    drive_api.client_folders("NEWCO", "Newco Towing LLC", gateway=gw)
    assert all(f.drive_id == CLAUDE for f in gw.created), (
        "every folder created must live in the Claude sandbox")


def test_flat_mode_keeps_the_previous_behaviour(monkeypatch):
    monkeypatch.setattr(drive_api, "FILING_MODE", "sandbox-flat")
    gw = FakeDriveGateway()
    clients_id, client_id, _ = drive_api.client_folders("LAKES", "Lakeside Towing", gateway=gw)
    assert gw.get(clients_id).name == "Clients"
    assert gw.get(client_id).parent_id == clients_id


def test_production_is_not_a_reachable_filing_mode(monkeypatch):
    monkeypatch.setattr(drive_api, "FILING_MODE", "production")
    gw = FakeDriveGateway()
    with pytest.raises(Exception):
        drive_api.client_folders("LAKES", "Lakeside Towing", gateway=gw)
