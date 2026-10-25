"""A recording ``EsdeStandInPass`` for library-sync tests, which places and removes nothing."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Collection

    from domain.library_surface import LibrarySurface
    from domain.work_unit import WorkUnit


class FakeEsdeStandInPass:
    """Records what a run hands the pass: the ``surfaces`` begun, each unit ``placed`` and the ``settles``.

    A unit is recorded as ``(unit, representative ids, skipped)``.
    """

    def __init__(self) -> None:
        self.surfaces: list[LibrarySurface] = []
        self.placed: list[tuple[WorkUnit, frozenset[int], bool]] = []
        self.settles = 0

    def begin_run(self, surface: LibrarySurface) -> None:
        self.surfaces.append(surface)

    async def place(
        self,
        unit: WorkUnit,
        roms: list[dict[str, Any]],
        representative_ids: Collection[int],
        *,
        skipped: bool = False,
    ) -> None:
        self.placed.append((unit, frozenset(representative_ids), skipped))

    async def settle(self) -> None:
        self.settles += 1
