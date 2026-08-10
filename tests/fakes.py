"""In-memory Google Drive double used by every drive_api / client_match test.

No test may hit the network (see this change's constraints), so this is the
ONLY DriveGateway implementation tests are allowed to construct — never
drive_api.GoogleDriveGateway, which wraps the real googleapiclient service.

FakeDriveGateway implements the same two-method surface as GoogleDriveGateway
(list_children / create_folder) against a plain in-memory dict, and it runs
every create through the real assert_writable() guard so a test that tries to
write outside the Claude drive fails the exact same way production code
would.
"""
from __future__ import annotations

import itertools

from drive_api import FOLDER_MIME, DriveItem, assert_writable


class FakeDriveGateway:
    def __init__(self, items: list[DriveItem] | None = None) -> None:
        self._items: dict[str, DriveItem] = {it.id: it for it in (items or [])}
        self._seq = itertools.count(1)
        # Every folder this gateway created, in order — lets a test assert both
        # WHERE something landed and that nothing was created outside the sandbox.
        self.created: list[DriveItem] = []

    # ---- DriveGateway protocol ---------------------------------------
    def list_children(self, parent_id: str, drive_id: str) -> list[DriveItem]:
        return [it for it in self._items.values()
                if it.parent_id == parent_id and it.drive_id == drive_id]

    def create_folder(self, name: str, parent_id: str, drive_id: str) -> DriveItem:
        assert_writable(drive_id)  # same guard GoogleDriveGateway uses - never bypassed
        new_id = f"fake-{next(self._seq)}"
        item = DriveItem(id=new_id, name=name, mime_type=FOLDER_MIME,
                          drive_id=drive_id, parent_id=parent_id)
        self._items[new_id] = item
        self.created.append(item)
        return item

    # ---- fixture-building helpers (test-only, not part of the protocol) ----
    def add(self, item: DriveItem) -> DriveItem:
        self._items[item.id] = item
        return item

    def folder(self, id: str, name: str, parent_id: str, drive_id: str) -> DriveItem:
        return self.add(DriveItem(id=id, name=name, mime_type=FOLDER_MIME,
                                   drive_id=drive_id, parent_id=parent_id))

    def file(self, id: str, name: str, parent_id: str, drive_id: str,
              mime_type: str = "application/pdf") -> DriveItem:
        return self.add(DriveItem(id=id, name=name, mime_type=mime_type,
                                   drive_id=drive_id, parent_id=parent_id))

    def count(self) -> int:
        return len(self._items)

    def get(self, item_id: str) -> DriveItem:
        return self._items[item_id]
