"""A recording ``EsdeStandInGuard`` for the services that clear, restore and look past stand-ins."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable


class FakeEsdeStandInGuard:
    """Stand-ins as a path → game map, with every clear and restore recorded in ``calls`` in the order made.

    A clear removes the game's paths from ``stand_ins`` and hands each to
    ``unlink``, which removes a link on disk by default, the way the real
    service removes its own; it answers ``clear_answer``. A restore only
    records. ``on_call`` runs at each clear and restore with its name and game,
    for a test that reads the caller's state at that moment.
    """

    def __init__(self, stand_ins: dict[str, int] | None = None) -> None:
        self.stand_ins: dict[str, int] = dict(stand_ins or {})
        self.calls: list[tuple[str, int]] = []
        self.clear_answer = True
        self.on_call: Callable[[str, int], None] | None = None
        self.unlink: Callable[[str], None] = _unlink_a_link

    def do_stand_in_at(self, path: str) -> int | None:
        if path in self.stand_ins:
            return self.stand_ins[path]
        return self.stand_ins.get(os.path.dirname(path))

    async def clear_for_download(self, rom_id: int) -> bool:
        self._record("clear", rom_id)
        for path in [path for path, owner in self.stand_ins.items() if owner == rom_id]:
            del self.stand_ins[path]
            self.unlink(path)
        return self.clear_answer

    async def restore(self, rom_id: int) -> bool:
        self._record("restore", rom_id)
        return True

    def _record(self, name: str, rom_id: int) -> None:
        self.calls.append((name, rom_id))
        if self.on_call is not None:
            self.on_call(name, rom_id)


def _unlink_a_link(path: str) -> None:
    if os.path.islink(path):
        os.unlink(path)
