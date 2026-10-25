"""In-memory ``EsdeStandInStore`` and ``EsdeStandInIndexStore`` for service tests."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

from domain.esde_stand_in import KEPT, OCCUPIED, PLACED

if TYPE_CHECKING:
    from collections.abc import Mapping

    from domain.esde_stand_in import PlaceOutcome, StandInRecord, StandInShape


class FakeEsdeStandInStore:
    """Stand-ins as a path → ``(rom_id, shape)`` map, beside the paths something else holds.

    ``others`` are paths a file, a folder or a link of somebody else's holds:
    nothing is ever placed over or removed from one. ``fail`` holds paths whose
    every change raises ``OSError``. ``removed_stubs`` lists every game whose
    stubs were removed.
    """

    def __init__(self) -> None:
        self.links: dict[str, tuple[int, StandInShape]] = {}
        self.others: set[str] = set()
        self.fail: set[str] = set()
        self.removed_stubs: list[int] = []

    def place(self, rom_id: int, shape: StandInShape, path: str) -> PlaceOutcome:
        if path in self.fail:
            raise OSError(f"cannot write {path}")
        if path in self.others:
            return OCCUPIED
        held = self.links.get(path)
        if held is not None and held[0] != rom_id:
            return OCCUPIED
        self.links[path] = (rom_id, shape)
        return KEPT if held == (rom_id, shape) else PLACED

    def remove(self, rom_id: int, path: str) -> bool:
        if self.stand_in_at(path) != rom_id:
            return False
        if path in self.fail:
            raise OSError(f"cannot remove {path}")
        del self.links[path]
        return True

    def remove_stub(self, rom_id: int) -> None:
        self.removed_stubs.append(rom_id)

    def stand_in_at(self, path: str) -> int | None:
        held = self.links.get(path)
        return None if held is None else held[0]

    def list_in(self, folder: str) -> dict[str, int]:
        return {path: rom_id for path, (rom_id, _shape) in self.links.items() if os.path.dirname(path) == folder}


class FakeEsdeStandInIndex:
    """The record as a dict; ``writes`` counts every write, and ``fail_write`` makes one raise ``OSError``."""

    def __init__(self, index: Mapping[int, StandInRecord] | None = None) -> None:
        self.index: dict[int, StandInRecord] = dict(index or {})
        self.writes = 0
        self.fail_write = False

    def read(self) -> dict[int, StandInRecord]:
        return dict(self.index)

    def write(self, index: Mapping[int, StandInRecord]) -> None:
        if self.fail_write:
            raise OSError("cannot write the record")
        self.writes += 1
        self.index = dict(index)
