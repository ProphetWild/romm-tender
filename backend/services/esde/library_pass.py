"""The ES-DE pass the library sync hands its units to: the stand-ins first, then the media named after them.

A game's media are named after the entry ES-DE lists it as, and that entry is
the stand-in service's to settle — its stand-in, or what its download left —
so the stand-ins of a unit are placed, and those of a run's end removed, before
the media service is handed what they answered. The pass is also what the
settings tell when the library surface is stored, and tells both in turn.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Collection

    from domain.library_surface import LibrarySurface
    from domain.work_unit import WorkUnit
    from services.esde.media import EsdeMediaService
    from services.esde.stand_ins import EsdeStandInService


@dataclass(frozen=True)
class EsdeLibraryPassConfig:
    """Frozen wiring bundle handed to ``EsdeLibraryPass.__init__``: the two services it hands each call to."""

    stand_ins: EsdeStandInService
    media: EsdeMediaService


class EsdeLibraryPass:
    """Hands each of a sync's units and its end to the stand-in service, then to the media service.

    Implements the ``EsdeStandInPass`` Protocol the library sync holds and the
    ``LibrarySurfaceListener`` the settings tell.
    """

    def __init__(self, *, config: EsdeLibraryPassConfig) -> None:
        self._stand_ins = config.stand_ins
        self._media = config.media

    def begin_run(self, surface: LibrarySurface) -> None:
        """Start an apply run that syncs to *surface*."""
        self._stand_ins.begin_run(surface)

    async def place(
        self,
        unit: WorkUnit,
        roms: list[dict[str, Any]],
        representative_ids: Collection[int],
        *,
        skipped: bool = False,
    ) -> None:
        """Place *unit*'s stand-ins, then queue each representative's media under the entry it is listed as.

        A unit *skipped* as unchanged hands over only the ROMs bound to
        shortcuts, rebuilt from the sync's own record, so its media are only
        moved to where their entries stand now and nothing is fetched for them.
        """
        entries = await self._stand_ins.place(unit, roms, representative_ids, skipped=skipped)
        await self._media.queue(entries, {} if skipped else {int(rom["id"]): rom for rom in roms})

    async def settle(self) -> None:
        """End an apply run that was not cancelled: the stand-ins settle, then the media of what they no longer keep go.

        The media of a game whose download now holds it move to the entry the
        download left.
        """
        settled = await self._stand_ins.settle()
        if settled is None:
            return
        await self._media.settle(settled.scope)
        await self._media.queue(settled.entries, {})

    async def surface_stored(self, surface: LibrarySurface) -> None:
        """Tell the stand-ins, then the media, the library surface stored."""
        await self._stand_ins.surface_stored(surface)
        await self._media.surface_stored(surface)
