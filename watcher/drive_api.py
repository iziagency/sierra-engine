"""Direct Google Drive uploads via the API — no desktop-app sync dependency.

Files land in the cloud (Drive web) instantly. Uses OAuth for the user's own
Google account, which already has access to the "Claude" shared drive.

First run does a one-time browser authorization and caches token.json.
After that it refreshes silently.

Setup:
  put the downloaded OAuth client JSON at  watcher/oauth_client.json
  then run:  python drive_api.py authorize
  (opens a browser once; approve; done)

Usage from code:
  from drive_api import upload_to_drive
  link = upload_to_drive(local_path, dest_name)   # returns webViewLink

--------------------------------------------------------------------------
Multi-drive client-folder resolution (2026-07-27 fix)
--------------------------------------------------------------------------
Claude / Clients / Docs / Prospects are FOUR SEPARATE shared drives, not
folders inside one drive — verified against the live API, see
scratchpad/drive_inventory.json for the captured shape. The client is
mid-migration from nested (legacy, filed under a broker's initials) to flat
per-client folders, so both shapes exist in production right now:

  flat:   Prospects/LAKES Lakeside Towing
  nested: Prospects/Prospects CG/F&FTO F & F Towing Service

Folder identity is always the SP code — the leading whitespace-delimited
token of the folder's name — never the display name, which is spelled
inconsistently across intakes. Real folders use "<SP> <Name>" (a single
space, no dash); this module used to create "<SP> - <DBA>" (dash) inside the
WRONG drive (Claude, not Clients/Prospects) — see client_folders() below.

Everything that can WRITE (files.create/update/copy/delete) may only ever
target the Claude sandbox drive. Prospects/Clients/Docs are READ-ONLY in this
codebase: only files.list / files.get. assert_writable() enforces this as
code, not just a comment, and every write path in this module runs through
it (directly, or via GoogleDriveGateway.create_folder).
"""
from __future__ import annotations

import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from google.auth.transport.requests import Request
from google.oauth2 import service_account
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

HERE = Path(__file__).resolve().parent
CLIENT_JSON = HERE / "oauth_client.json"
TOKEN_JSON = HERE / "token.json"
# Present only on an unattended install. See get_creds() for why it wins.
SERVICE_ACCOUNT_JSON = HERE / "service_account.json"
SCOPES = ["https://www.googleapis.com/auth/drive"]

# The "Claude" shared drive. Filled by `python drive_api.py find-drive`.
SHARED_DRIVE_NAME = "Claude"
CONFIG = HERE / "drive_config.txt"   # stores the resolved shared drive id


def get_creds() -> Credentials:
    """Drive credentials — a service-account key if one is installed, else OAuth.

    The personal OAuth client's consent screen is still in *testing* mode, and
    Google expires those refresh tokens after seven days. A human notices and
    re-runs authorize_url.py; a machine running unattended just starts failing
    every drop, on whatever day of the week the seventh happens to fall.

    A service account has no consent screen and nothing to expire. JC adds its
    address to the Claude shared drive the same way he would add a person, and
    the credential lasts as long as the key file does.

    The key file is the ONLY trigger, so a workstation without one keeps
    behaving exactly as before — and when the key is present token.json is
    never written, because a server quietly rewriting a human's cached
    credential is the kind of thing an audit finds and cannot explain.
    """
    if SERVICE_ACCOUNT_JSON.exists():
        return service_account.Credentials.from_service_account_file(
            str(SERVICE_ACCOUNT_JSON), scopes=SCOPES)
    creds = None
    if TOKEN_JSON.exists():
        creds = Credentials.from_authorized_user_file(str(TOKEN_JSON), SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not CLIENT_JSON.exists():
                raise SystemExit(
                    f"Missing {CLIENT_JSON.name}. Download the OAuth client JSON "
                    f"from Google Cloud Console and save it there."
                )
            flow = InstalledAppFlow.from_client_secrets_file(str(CLIENT_JSON), SCOPES)
            creds = flow.run_local_server(port=0)
        TOKEN_JSON.write_text(creds.to_json(), encoding="utf-8")
    return creds


def service():
    return build("drive", "v3", credentials=get_creds())


def resolve_shared_drive_id(svc=None) -> str:
    if CONFIG.exists():
        return CONFIG.read_text(encoding="utf-8").strip()
    svc = svc or service()
    res = svc.drives().list(pageSize=100).execute()
    for d in res.get("drives", []):
        if d["name"] == SHARED_DRIVE_NAME:
            CONFIG.write_text(d["id"], encoding="utf-8")
            return d["id"]
    raise SystemExit(f"Shared drive '{SHARED_DRIVE_NAME}' not found in your account.")


FOLDER_MIME = "application/vnd.google-apps.folder"


# ============================================================================
# Drive identities — verified 2026-07-27 against the live API (see
# scratchpad/drive_inventory.json). These four ids are real and stable;
# never re-derive them from a display name (drive NAMES are not unique the
# way ids are, and a typo'd lookup is exactly the kind of accident the write
# guard below exists to catch).
# ============================================================================
CLAUDE_DRIVE_ID = "0AHrsw6nAUNQlUk9PVA"        # our sandbox — the ONLY writable drive
CLIENTS_DRIVE_ID = "0AB_uKWCLcC98Uk9PVA"       # production — READ ONLY
DOCS_DRIVE_ID = "0ADoBjW1QKJCqUk9PVA"          # production — READ ONLY
PROSPECTS_DRIVE_ID = "0AK1WtHrcveX_Uk9PVA"     # production — READ ONLY


# ----------------------------------------------------------------------------
# THE single explicit configuration point for "which drive do we write to".
#
# This is a plain source-level constant on purpose, not an environment
# variable: an env var can be typo'd, inherited from a stale shell profile, or
# left over from someone else's session, and would then silently point real
# writes at a production client's Drive. Changing this value is a deliberate,
# reviewed code change — never a per-run flag.
#
# Do not "improve" this into a name->id lookup table for convenience — that
# is exactly the kind of accidental-opt-in this constant exists to prevent.
# ----------------------------------------------------------------------------
WRITE_TARGET_DRIVE_ID = CLAUDE_DRIVE_ID


class DriveWriteBlocked(Exception):
    """Raised when code tries to write outside the Claude sandbox drive."""


def assert_writable(drive_id: str) -> None:
    """Hard gate in front of every create/update/copy/delete call.

    Deliberately independent of WRITE_TARGET_DRIVE_ID above: even if that
    constant were ever repointed, this still refuses anything that is not the
    literal Claude sandbox drive, because today NOTHING else may be written
    to — Prospects/Clients/Docs are production drives this codebase only
    ever reads. Loosening this is a separate, deliberate, reviewed change,
    not a side effect of a config edit. Mirrors the same pattern already
    proven in mirror_to_sandbox.py::assert_in_claude.
    """
    if drive_id != CLAUDE_DRIVE_ID:
        raise DriveWriteBlocked(
            f"refusing to write to drive {drive_id!r}: only the Claude sandbox "
            f"drive ({CLAUDE_DRIVE_ID}) accepts writes. Prospects/Clients/Docs "
            f"are read-only production drives in this codebase."
        )


@dataclass(frozen=True)
class DriveItem:
    """One file or folder, as returned by a DriveGateway."""
    id: str
    name: str
    mime_type: str
    drive_id: str
    parent_id: str | None = None

    @property
    def is_folder(self) -> bool:
        return self.mime_type == FOLDER_MIME


class DriveGateway(Protocol):
    """Minimal read/write surface the resolver needs.

    GoogleDriveGateway (below) is the only real implementation; tests use
    tests/fakes.py::FakeDriveGateway. Nothing outside this module and its
    gateway implementations should ever build a raw `q=` Drive query — that
    query language belongs entirely to GoogleDriveGateway, so nothing else
    can get its escaping wrong.
    """

    def list_children(self, parent_id: str, drive_id: str) -> list[DriveItem]:
        ...

    def create_folder(self, name: str, parent_id: str, drive_id: str) -> DriveItem:
        ...


class GoogleDriveGateway:
    """Real Drive I/O, scoped to one shared drive per call.

    list_children lists ALL children (no server-side name filter — same
    approach already proven in mirror_to_sandbox.py::children()) so callers
    never have to hand-escape a name into a `q=` string. create_folder always
    runs through assert_writable first, independent of any caller, so a
    mistake elsewhere in the codebase can't turn this into a write against a
    production drive.
    """

    def __init__(self, svc) -> None:
        self._svc = svc

    def list_children(self, parent_id: str, drive_id: str) -> list[DriveItem]:
        out: list[DriveItem] = []
        page = None
        while True:
            res = self._svc.files().list(
                q=f"'{parent_id}' in parents and trashed = false",
                corpora="drive", driveId=drive_id,
                includeItemsFromAllDrives=True, supportsAllDrives=True,
                fields="nextPageToken, files(id, name, mimeType, parents, driveId)",
                pageSize=1000, pageToken=page,
            ).execute()
            for f in res.get("files", []):
                out.append(DriveItem(
                    id=f["id"], name=f["name"], mime_type=f["mimeType"],
                    drive_id=f.get("driveId", drive_id),
                    parent_id=(f.get("parents") or [None])[0],
                ))
            page = res.get("nextPageToken")
            if not page:
                break
        return out

    def create_folder(self, name: str, parent_id: str, drive_id: str) -> DriveItem:
        assert_writable(drive_id)
        created = self._svc.files().create(
            body={"name": name, "mimeType": FOLDER_MIME, "parents": [parent_id]},
            supportsAllDrives=True, fields="id, name, mimeType, parents, driveId",
        ).execute()
        return DriveItem(
            id=created["id"], name=created.get("name", name),
            mime_type=created.get("mimeType", FOLDER_MIME),
            drive_id=created.get("driveId", drive_id), parent_id=parent_id,
        )


@dataclass(frozen=True)
class DriveRoot:
    """One place to start a search: a shared drive, optionally scoped to a
    subfolder (the sandbox mirror lives inside a subfolder of Claude — see
    sandbox_mirror_root)."""
    label: str                          # "Prospects" / "Clients" / ... — also
                                         # the legacy container-prefix marker
                                         # for THIS root (see _walk_identity_folders)
    drive_id: str
    root_folder_id: str | None = None   # None = the shared drive's own root


# Production, read-only search roots for "does this client already have a
# Drive folder" lookups. Docs is deliberately excluded from the default: it
# only ever holds Apps / Bind packets / Knowledge base / OnPoint info
# (verified against the live inventory) — never client folders.
PROSPECTS_ROOT = DriveRoot("Prospects", PROSPECTS_DRIVE_ID)
CLIENTS_ROOT = DriveRoot("Clients", CLIENTS_DRIVE_ID)
DOCS_ROOT = DriveRoot("Docs", DOCS_DRIVE_ID)
DEFAULT_SEARCH_ROOTS: tuple[DriveRoot, ...] = (PROSPECTS_ROOT, CLIENTS_ROOT)

# Must match mirror_to_sandbox.py::SANDBOX exactly — that script copies
# production into Claude/<this>/<Prospects|Clients|Docs>/... so sandbox
# tests/dry runs can resolve against a faithful read-only copy without ever
# touching the real production drives.
SANDBOX_MIRROR_PREFIX = "_TEST COPIES - not client deliverables"

# Where finished documents get filed. JC's ask was "drop it back in that same
# folder" — the client's own folder, next to their loss runs and QP. Production
# is read-only in code, so "sandbox-mirror" is the faithful rehearsal: output
# lands beside the very files it would sit beside in production, inside the
# sandbox. "sandbox-flat" is the older synthetic Claude/Clients/<SP> layout,
# kept so nothing that depends on it breaks. Filing into production is
# deliberately NOT a mode here — that switch belongs with the service-account
# migration, not a string someone can flip by accident.
FILING_MODE = "sandbox-mirror"


@dataclass(frozen=True)
class FolderMatch:
    """One candidate hit for an SP code."""
    sp_code: str
    name: str                # full folder name, e.g. "LAKES Lakeside Towing"
    id: str
    drive_id: str
    root_label: str           # which configured root this came from
    path: str                 # path relative to the root's search point
    nested: bool               # False = flat (depth 0), True = one level under
                                # a legacy broker-initials container


class AmbiguousClientFolderError(Exception):
    """Raised when an SP code matches more than one folder.

    Filing a document into the wrong client's folder is the worst outcome
    this pipeline can produce — worse than failing loudly — so callers get
    every candidate and must resolve the conflict themselves; nothing here
    ever guesses.
    """

    def __init__(self, sp_code: str, matches: Sequence[FolderMatch]) -> None:
        self.sp_code = sp_code
        self.matches = list(matches)
        locations = "; ".join(f"{m.root_label}/{m.path}" for m in self.matches)
        super().__init__(
            f"SP code {sp_code!r} matches {len(self.matches)} folders — refusing "
            f"to guess which one is correct: {locations}"
        )


@dataclass(frozen=True)
class ResolveResult:
    sp_code: str
    matches: tuple[FolderMatch, ...]

    @property
    def is_ambiguous(self) -> bool:
        return len(self.matches) > 1

    @property
    def found(self) -> bool:
        return len(self.matches) > 0

    def unique(self) -> FolderMatch | None:
        """The single match, the None if there are zero, or raise if there is
        more than one. This is the only way this module ever turns "more than
        one candidate" into a decision — and that decision is always "stop",
        never "pick the first one"."""
        if self.is_ambiguous:
            raise AmbiguousClientFolderError(self.sp_code, self.matches)
        return self.matches[0] if self.matches else None


def _leading_token(name: str) -> str:
    return name.split(" ", 1)[0]


def parse_client_folder_name(name: str) -> tuple[str, str]:
    """Split a client folder's display name into (sp_code, dba).

    Handles both the real convention ("LAKES Lakeside Towing" — space, no
    dash) and the legacy one this pipeline mistakenly created ("BROOK1 -
    Brookfield Towing, LLC" — dash) so already-created sandbox folders
    keep parsing correctly. The SP code is always the leading
    whitespace-delimited token; an optional "- " right after it is stripped
    too. A folder that is only ever an SP code with no trailing name (real
    example: "Prospects open/GUSTA1") returns a blank dba, not a guess.
    """
    sp, _, rest = name.partition(" ")
    rest = rest.strip()
    if rest.startswith("- "):
        rest = rest[2:].strip()
    return sp, rest


def _walk_identity_folders(gateway: DriveGateway, roots: Sequence[DriveRoot]):
    """Yield every client/prospect identity folder across `roots`: flat
    entries directly under the root, and — exactly one level deeper — the
    ones filed under a legacy broker-initials container (a root-level folder
    whose own leading token equals the root's own label, e.g. "Clients AB",
    "Prospects open"; verified against every real container name in
    drive_inventory.json).

    This never recurses past that second level. Going deeper would start
    reading ordinary per-client project folders ("CAP 2025 KBK", "Certs",
    "WC lost 2024", ...) as if they were more clients, which they are not —
    almost every real client/prospect identity in the inventory sits at depth
    0 or depth 1, never deeper.

    KNOWN GAP (found in the inventory, not fabricated): "Clients inactive"
    contains one further sub-container, "Inactive archive", holding ~11 real
    retired clients (e.g. "WALKE1 Walker Painting") a third level down. This
    function does not reach them — a caller that needs that specific archive
    can still do so today by passing an extra DriveRoot whose root_folder_id
    already points at "Inactive archive" (see sandbox_mirror_root for the
    same pattern applied to the sandbox mirror).
    """
    for root in roots:
        parent = root.root_folder_id or root.drive_id
        for child in gateway.list_children(parent, root.drive_id):
            if not child.is_folder:
                continue
            if _leading_token(child.name) == root.label:
                for grandchild in gateway.list_children(child.id, root.drive_id):
                    if not grandchild.is_folder:
                        continue
                    yield FolderMatch(
                        sp_code=_leading_token(grandchild.name), name=grandchild.name,
                        id=grandchild.id, drive_id=grandchild.drive_id,
                        root_label=root.label, path=f"{child.name}/{grandchild.name}",
                        nested=True,
                    )
            else:
                yield FolderMatch(
                    sp_code=_leading_token(child.name), name=child.name,
                    id=child.id, drive_id=child.drive_id,
                    root_label=root.label, path=child.name, nested=False,
                )


def find_candidates(gateway: DriveGateway, sp_code: str,
                     roots: Sequence[DriveRoot]) -> list[FolderMatch]:
    """Flat-then-nested search for `sp_code` across `roots`. Read-only —
    never creates anything, regardless of which drives `roots` point at."""
    sp_code = sp_code.strip().upper()
    return [m for m in _walk_identity_folders(gateway, roots) if m.sp_code == sp_code]


def list_all_identity_folders(gateway: DriveGateway,
                               roots: Sequence[DriveRoot]) -> list[FolderMatch]:
    """Every client/prospect identity folder across `roots`, flat and nested,
    unfiltered by SP code. Used to seed client_match's fuzzy-name index from
    Drive (a full, read-only survey), as opposed to find_candidates, which
    looks for one specific code."""
    return list(_walk_identity_folders(gateway, roots))


def resolve_client_folder(gateway: DriveGateway, sp_code: str,
                           roots: Sequence[DriveRoot]) -> ResolveResult:
    """The resolver: given an SP code, find its existing folder(s) across
    `roots`. Always read-only. Returns every candidate — see ResolveResult
    and AmbiguousClientFolderError for how ambiguity is surfaced rather than
    guessed."""
    sp_code = sp_code.strip().upper()
    return ResolveResult(sp_code, tuple(find_candidates(gateway, sp_code, roots)))


def _find_child_folder(gateway: DriveGateway, name: str, parent_id: str,
                        drive_id: str) -> DriveItem | None:
    return next((c for c in gateway.list_children(parent_id, drive_id)
                 if c.is_folder and c.name == name), None)


def sandbox_mirror_root(gateway: DriveGateway, label: str) -> DriveRoot:
    """Build a DriveRoot pointing at the read-only mirror of `label`
    (Prospects/Clients/Docs) that mirror_to_sandbox.py copies into the Claude
    drive, so resolution can be exercised — in tests, or as a manual sandbox
    dry run — without ever touching the real production drives.

    Raises LookupError if the mirror (or the requested label under it)
    hasn't been created yet.
    """
    mirror = _find_child_folder(gateway, SANDBOX_MIRROR_PREFIX, CLAUDE_DRIVE_ID, CLAUDE_DRIVE_ID)
    if mirror is None:
        raise LookupError(
            f"{SANDBOX_MIRROR_PREFIX!r} not found in the Claude drive — run "
            f"mirror_to_sandbox.py first."
        )
    labeled = _find_child_folder(gateway, label, mirror.id, CLAUDE_DRIVE_ID)
    if labeled is None:
        raise LookupError(f"{label!r} has not been mirrored under {SANDBOX_MIRROR_PREFIX!r}")
    return DriveRoot(label=label, drive_id=CLAUDE_DRIVE_ID, root_folder_id=labeled.id)


def ensure_folder(name: str, parent_id: str | None = None, svc=None,
                   gateway: DriveGateway | None = None) -> str:
    """Return the id of folder `name` under parent (write-target drive root
    if None), creating it if it doesn't exist. Idempotent. Always operates on
    the write-target drive — see WRITE_TARGET_DRIVE_ID."""
    drive_id = WRITE_TARGET_DRIVE_ID
    assert_writable(drive_id)
    gw = gateway or GoogleDriveGateway(svc or service())
    parent = parent_id or drive_id
    existing = _find_child_folder(gw, name, parent, drive_id)
    if existing:
        return existing.id
    return gw.create_folder(name, parent, drive_id).id


def filing_roots(gateway: DriveGateway, svc=None) -> tuple[list[DriveRoot], str]:
    """Where to look for a client folder, and where a new one goes.

    Returns (roots to search, parent id for a brand-new client). A new client is
    a prospect until the policy binds, so it is created under Prospects — which
    is also how the real Drive is organised.
    """
    if FILING_MODE == "sandbox-mirror":
        try:
            prospects = sandbox_mirror_root(gateway, "Prospects")
            clients = sandbox_mirror_root(gateway, "Clients")
        except LookupError:
            # No mirror on this machine yet. Filing something somewhere sane
            # beats refusing to file at all, so fall back to the flat layout.
            return _flat_filing_roots(gateway, svc)
        return [prospects, clients], prospects.root_folder_id

    if FILING_MODE == "sandbox-flat":
        return _flat_filing_roots(gateway, svc)

    raise ValueError(
        f"unknown FILING_MODE {FILING_MODE!r} — expected 'sandbox-mirror' or "
        f"'sandbox-flat'. Filing into production is not selectable here."
    )


def _flat_filing_roots(gateway: DriveGateway, svc=None) -> tuple[list[DriveRoot], str]:
    clients_id = ensure_folder("Clients", svc=svc, gateway=gateway)
    root = DriveRoot(label="Clients", drive_id=WRITE_TARGET_DRIVE_ID,
                      root_folder_id=clients_id)
    return [root], clients_id


def client_folders(sp: str, dba: str, svc=None,
                    gateway: DriveGateway | None = None) -> tuple[str, str, str]:
    """Find-or-create Clients/<SP> <DBA>/ and its _source subfolder, inside
    the write-target drive (the Claude sandbox by default — WRITE_TARGET_DRIVE_ID).

    Reuses ANY existing folder whose leading token is `sp`, regardless of
    whether it was named with the legacy " - " separator or the real
    "<SP> <Name>" (space, no dash) convention, so folders the earlier bug
    already created are not orphaned by this fix. New folders always use the
    real convention. Raises AmbiguousClientFolderError instead of guessing if
    more than one folder already matches `sp` — see ResolveResult.unique().

    Returns (clients_id, client_id, source_id).
    """
    drive_id = WRITE_TARGET_DRIVE_ID
    assert_writable(drive_id)
    gw = gateway or GoogleDriveGateway(svc or service())

    roots, default_parent = filing_roots(gw, svc=svc)
    match = resolve_client_folder(gw, sp, roots).unique()  # raises if ambiguous

    if match:
        client_id = match.id
    else:
        client_id = gw.create_folder(f"{sp} {dba}", default_parent, drive_id).id
    clients_id = default_parent

    # NOTE: bound-policy subfolders ("Quote files" / "Endorsement files" / ...,
    # see docs/jc-knowledge/03-quoting-packet.md #5.4) belong under a specific
    # POLICY-YEAR folder (e.g. "CAP 2025 KBK"), which nothing in this pipeline
    # creates yet. A prior quote_files_folder() helper attached them to this
    # client-level folder instead — the wrong shape, and never called from
    # anywhere — and was removed rather than fixed blind. Wire the real thing
    # up once policy-year folders exist here.
    source_id = ensure_folder("_source", parent_id=client_id, svc=svc, gateway=gw)
    return clients_id, client_id, source_id


def folder_link(folder_id: str) -> str:
    return f"https://drive.google.com/drive/folders/{folder_id}"


def upload_to_drive(local_path: str, dest_name: str, parent_id: str | None = None) -> str:
    """Upload/overwrite a file into the Claude shared drive root (or parent_id).

    Returns the file's webViewLink. If a file with dest_name already exists in
    the target folder, it's updated in place (keeps the same link, mirrors the
    'live draft, not duplicates' rule).
    """
    svc = service()
    drive_id = resolve_shared_drive_id(svc)
    target_parent = parent_id or drive_id

    # look for an existing file of the same name to update in place
    q = (f"name = '{dest_name}' and '{target_parent}' in parents and trashed = false")
    existing = svc.files().list(
        q=q, corpora="drive", driveId=drive_id,
        includeItemsFromAllDrives=True, supportsAllDrives=True,
        fields="files(id)",
    ).execute().get("files", [])

    media = MediaFileUpload(local_path, resumable=False)
    if existing:
        f = svc.files().update(
            fileId=existing[0]["id"], media_body=media,
            supportsAllDrives=True, fields="id, webViewLink",
        ).execute()
    else:
        f = svc.files().create(
            body={"name": dest_name, "parents": [target_parent]},
            media_body=media, supportsAllDrives=True,
            fields="id, webViewLink",
        ).execute()
    return f.get("webViewLink", "")


def _main() -> None:
    cmd = sys.argv[1] if len(sys.argv) > 1 else "authorize"
    if cmd == "authorize":
        get_creds()
        print("Authorized. token.json cached.")
        print("Shared drive id:", resolve_shared_drive_id())
    elif cmd == "find-drive":
        svc = service()
        for d in svc.drives().list(pageSize=100).execute().get("drives", []):
            print(f"{d['name']}  ->  {d['id']}")
    elif cmd == "test-upload":
        # uploads this script itself as a connectivity test
        link = upload_to_drive(str(Path(__file__)), "drive_api_test.txt")
        print("uploaded, link:", link)
    else:
        print(__doc__)


if __name__ == "__main__":
    _main()
