"""A server cannot be woken up every seven days to click a consent screen.

The engine authenticates to Drive with a personal OAuth client whose consent
screen is still in *testing* mode, and Google expires those refresh tokens
after 7 days. On a staffed workstation that is a mild annoyance — someone runs
`authorize_url.py` again. On the Mac Studio running unattended it is an outage
that starts on a Sunday and is noticed on a Monday, with every drop in between
answered by a stack trace.

A service account has no consent screen and no refresh token to expire. JC adds
its address to the Claude shared drive exactly as he would add a person, and the
credential then lives as long as the key file does.

The switch has to be *inert until the key exists*: the workstation that still
runs on OAuth must not change behaviour because a deployment guide was written.
So the only trigger is the presence of `service_account.json`, and when it is
present nothing may touch `token.json` — a server that silently rewrites a
human's cached token is a credential leak waiting for an audit.
"""
from __future__ import annotations

import json

import pytest

import drive_api


def write_key(path) -> None:
    """A structurally real service-account key, so the google-auth loader runs.

    Faking the loader would only prove our `if` works; signing a real key
    proves the credential google-auth hands back is one Drive would accept.
    """
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("ascii")
    path.write_text(json.dumps({
        "type": "service_account",
        "project_id": "sierra-engine",
        "private_key_id": "test",
        "private_key": pem,
        "client_email": "sierra-engine@sierra-engine.iam.gserviceaccount.com",
        "client_id": "1",
        "token_uri": "https://oauth2.googleapis.com/token",
    }), encoding="utf-8")


@pytest.fixture
def keyfile(tmp_path, monkeypatch):
    p = tmp_path / "service_account.json"
    write_key(p)
    monkeypatch.setattr(drive_api, "SERVICE_ACCOUNT_JSON", p)
    return p


def test_a_key_file_is_the_credential(keyfile, tmp_path, monkeypatch):
    monkeypatch.setattr(drive_api, "TOKEN_JSON", tmp_path / "token.json")
    creds = drive_api.get_creds()
    assert creds.service_account_email.endswith(".iam.gserviceaccount.com")


def test_the_key_carries_the_drive_scope(keyfile, tmp_path, monkeypatch):
    # Without the scope the credential authenticates and then 403s on every
    # call — the worst kind of failure, because it looks like a permissions
    # problem on JC's side rather than ours.
    monkeypatch.setattr(drive_api, "TOKEN_JSON", tmp_path / "token.json")
    assert list(drive_api.get_creds().scopes) == drive_api.SCOPES


def test_a_service_account_never_writes_the_human_token(keyfile, tmp_path, monkeypatch):
    token = tmp_path / "token.json"
    monkeypatch.setattr(drive_api, "TOKEN_JSON", token)
    drive_api.get_creds()
    assert not token.exists()


def test_a_service_account_never_opens_a_browser(keyfile, tmp_path, monkeypatch):
    # run_local_server() on a headless service blocks forever holding a port.
    monkeypatch.setattr(drive_api, "TOKEN_JSON", tmp_path / "token.json")

    def explode(*a, **k):  # pragma: no cover - the point is that it is not called
        raise AssertionError("the OAuth consent flow must not run on a server")

    monkeypatch.setattr(drive_api.InstalledAppFlow, "from_client_secrets_file", explode)
    drive_api.get_creds()


def test_without_a_key_file_nothing_changes(tmp_path, monkeypatch):
    # The workstation still runs on the cached OAuth token, untouched.
    monkeypatch.setattr(drive_api, "SERVICE_ACCOUNT_JSON", tmp_path / "absent.json")
    monkeypatch.setattr(drive_api, "TOKEN_JSON", tmp_path / "token.json")
    monkeypatch.setattr(drive_api, "CLIENT_JSON", tmp_path / "absent_client.json")
    with pytest.raises(SystemExit):
        drive_api.get_creds()
