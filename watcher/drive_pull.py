"""Read a client's own paperwork out of Sierra Pacific's Prospects/Clients drives.

Until now the engine only knew what a broker had dropped into Slack, so a
client with fifteen years of history in Drive looked brand new. This pulls the
most recent CAP packet out of their real folder and turns it into a dossier —
the same path proven by hand on 8.2 for SOUTH5 and HAMIL, where a QP's own
AcroForm fields gave 9 vehicles and 11 drivers with no model call at all.

STRICTLY READ-ONLY. Prospects and Clients are production; the write gate in
drive_api refuses them, and nothing here uploads. Deliverables keep going to
the Claude sandbox.
"""
from __future__ import annotations

import io
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

# `<SP> CAP <risk> QP <M.D.YY>[ comp].pdf` and `<SP> CAP app <M.D.YY>.pdf`.
# The policy type must be CAP: a WC or garage packet answers different
# questions and would fill a commercial auto app with the wrong facts.
_QP = re.compile(r"\bCAP\b.*\bQP\b.*\.pdf$", re.I)
_APP = re.compile(r"\bCAP\b.*\bapp\b.*\.pdf$", re.I)
_DATE = re.compile(r"\b(\d{1,2})\.(\d{1,2})\.(\d{2})\b")
# "CAP lost 2024", "MTC lost 2025" — policies that did not bind. History.
_HISTORY = re.compile(r"\blost\b", re.I)


def is_history(folder_name: str) -> bool:
    """A folder holding a policy that was lost, not the working set."""
    return bool(_HISTORY.search(str(folder_name or "")))


def _stamp(name: str) -> tuple:
    """Sortable key: date first, then 'comp' as the tiebreaker.

    A dateless name sorts below every dated one rather than being dropped —
    it may be all the folder has.
    """
    m = _DATE.search(name)
    when = (int(m.group(3)), int(m.group(1)), int(m.group(2))) if m else (0, 0, 0)
    return when + (1 if re.search(r"\bcomp\b", name, re.I) else 0,)


def pick_source(file_names: list[str]) -> str | None:
    """The one file to build this client's CAP app from, or None.

    A QP always beats a bare app, even a newer one: the QP carries the app's
    pages plus the loss runs, COI and reports around them. Loss runs, COIs and
    non-CAP packets are never a source.
    """
    qps = [n for n in file_names if _QP.search(n)]
    if qps:
        return max(qps, key=_stamp)
    apps = [n for n in file_names if _APP.search(n)]
    if apps:
        return max(apps, key=_stamp)
    return None


# What a broker's paperwork actually arrives as. HEIC is on the list because
# they photograph apps on iPhones.
_MATERIAL = re.compile(r"\.(pdf|png|jpe?g|heic|webp|tiff?)$", re.I)
# Our own bookkeeping, which is not the client's paperwork.
_OURS = re.compile(r"^(Change History\.pdf|CHANGELOG\.md)$", re.I)
# One drop's worth. A folder with forty photographs is a scanning accident,
# and reading all of them costs real money — so the cap is reported, never
# silent.
MAX_MATERIALS = 12


def classify(file_names: list[str]) -> dict:
    """How to read this folder: {'mode', 'files', 'dropped'}.

    `packet` — a filled CAP QP or app is present, so its AcroForm fields are
    read directly: no model call, seconds, and the numbers are the ones Sierra
    already typed. `materials` — no packet, so the client's raw paperwork goes
    through the ordinary extraction. `empty` — nothing readable at all.
    """
    packet = pick_source(file_names)
    if packet:
        # The newest PACKET is not the newest FACT. CARRS carries a loss run
        # dated after its other paperwork, and a loss run is gospel — building
        # off the packet alone would produce an app that was already stale.
        cut = _stamp(packet)
        newer = [n for n in file_names
                 if n != packet and _MATERIAL.search(n) and not _OURS.match(n)
                 and _stamp(n)[:3] > (0, 0, 0) and _stamp(n)[:3] > cut[:3]]
        newer.sort(key=_stamp, reverse=True)
        return {"mode": "packet", "files": [packet], "dropped": 0,
                "newer": newer[:MAX_MATERIALS]}

    mats = [n for n in file_names if _MATERIAL.search(n) and not _OURS.match(n)]
    if not mats:
        return {"mode": "empty", "files": [], "dropped": 0, "newer": []}
    mats.sort(key=_stamp, reverse=True)
    return {"mode": "materials", "files": mats[:MAX_MATERIALS],
            "dropped": max(0, len(mats) - MAX_MATERIALS), "newer": []}


def find_client_folder(sp_code: str, gateway=None):
    """The production folder whose SP code is `sp_code`, or None. Read-only."""
    from drive_api import (CLIENTS_ROOT, PROSPECTS_ROOT, GoogleDriveGateway,
                           list_all_identity_folders, service)
    gw = gateway or GoogleDriveGateway(service())
    want = str(sp_code or "").strip().upper()
    for item in list_all_identity_folders(gw, [PROSPECTS_ROOT, CLIENTS_ROOT]):
        if item.name.split(" ")[0].upper() == want:
            return item
    return None


def pull_latest_packet(sp_code: str, dest_dir: Path, gateway=None) -> dict:
    """Download this client's newest CAP packet. Returns what happened.

    {'ok', 'file', 'source_name', 'folder', 'error'} — never raises for a
    missing client or an empty folder, because both are ordinary answers a
    broker needs to hear rather than a crash.
    """
    from googleapiclient.http import MediaIoBaseDownload

    from drive_api import GoogleDriveGateway, service
    svc = service()
    gw = gateway or GoogleDriveGateway(svc)

    folder = find_client_folder(sp_code, gw)
    if folder is None:
        return {"ok": False, "error": f"no folder for {sp_code} in Prospects or Clients"}

    children = gw.list_children(folder.id, folder.drive_id)
    files = {c.name: c for c in children if not c.is_folder}
    plan = classify(list(files))
    if plan["mode"] == "empty":
        return {"ok": False, "folder": folder.name, "mode": "empty",
                "error": f"{folder.name} holds nothing readable — "
                         f"{len(files)} file(s), none a CAP packet or a "
                         f"photograph of one"}

    dest_dir.mkdir(parents=True, exist_ok=True)
    downloaded = []
    fresher = []
    for name in list(plan["files"]) + list(plan.get("newer") or []):
        dest = dest_dir / name
        fh = io.FileIO(dest, "wb")
        dl = MediaIoBaseDownload(
            fh, svc.files().get_media(fileId=files[name].id, supportsAllDrives=True))
        done = False
        while not done:
            _, done = dl.next_chunk()
        fh.close()
        (fresher if name in (plan.get("newer") or []) else downloaded).append(str(dest))

    return {"ok": True, "mode": plan["mode"], "folder": folder.name,
            "files": downloaded, "source_name": plan["files"][0],
            "file": downloaded[0], "dropped": plan["dropped"],
            "newer": fresher}
