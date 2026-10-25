"""ES-DE's stand-ins: which game gets one in RetroDECK's ROM folder, where, and when it goes.

A stand-in is placed for each game the library sync gives a shortcut — its
sibling group's representative — that no download holds, at the path its
download would take; what a stand-in is, and that path, are
``domain/esde_stand_in.py``'s, and every file operation is the
``EsdeStandInStore``'s. Nothing changes unless the library surface writes
ES-DE: a sync's is the one it started with, and a single game's the setting's.

What Tender decided for each game is kept in its record
(``EsdeStandInIndexStore``): the path and shape of its stand-in, or that it gets
none, and the ROM's ``updated_at`` that decision was taken for, so an unchanged
ROM is never asked about again. A run removes nothing before it ends, and only
if it was not cancelled; then every stand-in of a game it no longer syncs goes,
and nothing that is not a stand-in ever does.

Every file read and write, every RomM request and every database read runs off
the event loop, and none inside an open Unit of Work. The record is changed
under one lock, so a sync's pass and a single game's question never interleave
their writes.
"""

from __future__ import annotations

import asyncio
import os
from collections import defaultdict
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from domain.emulator_sources import RETRODECK
from domain.esde_stand_in import (
    OCCUPIED,
    StandInRecord,
    SyncedScope,
    listing_settles_shape,
    predict_stand_in,
    wanted_links,
)
from domain.library_surface import DEFAULT_LIBRARY_SURFACE, library_surface, writes_esde
from domain.retrodeck_folders import FolderRefused
from lib.path_safety import PathTraversalError, safe_path_component

if TYPE_CHECKING:
    import logging
    from collections.abc import Collection, Mapping

    from domain.library_surface import LibrarySurface
    from domain.work_unit import WorkUnit
    from services.protocols import (
        DebugLogger,
        EsdeStandInIndexStore,
        EsdeStandInStore,
        PlatformSystems,
        RetroDeckFolders,
        RommRomReader,
        SystemM3uSupportFn,
        UnitOfWorkFactory,
    )


@dataclass(frozen=True)
class EsdeStandInServiceConfig:
    """Frozen wiring bundle handed to ``EsdeStandInService.__init__``.

    ``stand_ins`` does every file operation on a stand-in and ``index`` keeps the
    record of them. ``retrodeck_folders``, ``platform_systems`` and
    ``m3u_support`` answer a game's folder, system and playlist support as they
    answer a download's; ``romm_api`` answers the ROM detail a shape the listing
    cannot settle is predicted from; ``uow_factory`` reads which games are
    installed. ``settings`` is the live dict a single game's question reads the
    library surface from.
    """

    stand_ins: EsdeStandInStore
    index: EsdeStandInIndexStore
    retrodeck_folders: RetroDeckFolders
    platform_systems: PlatformSystems
    m3u_support: SystemM3uSupportFn
    romm_api: RommRomReader
    uow_factory: UnitOfWorkFactory
    settings: dict[str, Any]
    loop: asyncio.AbstractEventLoop
    logger: logging.Logger
    log_debug: DebugLogger


@dataclass
class _Run:
    """What the apply run in flight has handed the pass so far."""

    surface: LibrarySurface = DEFAULT_LIBRARY_SURFACE
    representatives: set[int] = field(default_factory=set)
    read_platforms: set[str] = field(default_factory=set)
    unchanged_platforms: set[str] = field(default_factory=set)
    unchanged_rom_ids: set[int] = field(default_factory=set)
    # The folders its fetched units placed into, where a stand-in the record
    # does not name is looked for at its end.
    swept_folders: set[str] = field(default_factory=set)

    def scope(self) -> SyncedScope:
        return SyncedScope(
            representatives=frozenset(self.representatives),
            read_platforms=frozenset(self.read_platforms),
            unchanged_platforms=frozenset(self.unchanged_platforms),
            unchanged_rom_ids=frozenset(self.unchanged_rom_ids),
        )


@dataclass(frozen=True, slots=True)
class _Decision:
    rom_id: int
    record: StandInRecord


class EsdeStandInService:
    """Places and removes ES-DE's stand-ins for the library sync, and answers for one game.

    Implements the ``EsdeStandInPass`` Protocol the sync holds.
    """

    def __init__(self, *, config: EsdeStandInServiceConfig) -> None:
        self._stand_ins = config.stand_ins
        self._index = config.index
        self._retrodeck_folders = config.retrodeck_folders
        self._platform_systems = config.platform_systems
        self._m3u_support = config.m3u_support
        self._romm_api = config.romm_api
        self._uow_factory = config.uow_factory
        self._settings = config.settings
        self._loop = config.loop
        self._logger = config.logger
        self._log_debug = config.log_debug
        self._lock = asyncio.Lock()
        self._run = _Run()

    # ── The library sync's pass ─────────────────────────────────────

    def begin_run(self, surface: LibrarySurface) -> None:
        """Start an apply run that syncs to *surface*, forgetting what an earlier one handed over."""
        self._run = _Run(surface=surface)

    async def place(
        self,
        unit: WorkUnit,
        roms: list[dict[str, Any]],
        representative_ids: Collection[int],
        *,
        skipped: bool = False,
    ) -> None:
        """Place a stand-in for each of *unit*'s *roms* among *representative_ids* that no download holds.

        *skipped* says the sync skipped *unit* as unchanged since its last
        apply, so *roms* are only those it bound to shortcuts, and every game
        recorded on its platform, or among its members, stays. A placement that
        fails is logged and the rest go ahead.
        """
        run = self._run
        if not writes_esde(run.surface):
            return
        if skipped and unit.type == "platform":
            run.unchanged_platforms.add(unit.slug)
        elif skipped:
            run.unchanged_rom_ids.update(unit.member_rom_ids)
        elif unit.type == "platform":
            run.read_platforms.add(unit.slug)
        wanted = [rom for rom in roms if rom.get("id") in representative_ids]
        run.representatives.update(int(rom["id"]) for rom in wanted)
        if not wanted:
            return
        installed = await self._loop.run_in_executor(None, self._installed_rom_ids_io)
        recorded = await self._loop.run_in_executor(None, self._index.read)
        pending = [rom for rom in wanted if int(rom["id"]) not in installed]
        decisions = await self._loop.run_in_executor(None, self._decide_io, pending, recorded)
        async with self._lock:
            folders = await self._loop.run_in_executor(None, self._place_io, decisions)
        if not skipped:
            run.swept_folders.update(folders)

    async def settle(self) -> None:
        """End an apply run that was not cancelled: every stand-in of a game it no longer syncs goes.

        A game it syncs and a download holds loses its stand-in and keeps its
        record. Ends the run, so a second call removes nothing.
        """
        run, self._run = self._run, _Run()
        if not writes_esde(run.surface):
            return
        installed = await self._loop.run_in_executor(None, self._installed_rom_ids_io)
        async with self._lock:
            removed = await self._loop.run_in_executor(None, self._settle_io, run, installed)
        self._log_debug(f"[esde] the run's end removed {removed} stand-ins")

    # ── One game ────────────────────────────────────────────────────

    async def is_stand_in(self, path: str) -> bool:
        """Whether *path* is a stand-in, or lies directly inside a stand-in that is a folder."""
        return await self._loop.run_in_executor(None, self._is_stand_in_io, path)

    async def stand_in_for(self, rom_id: int) -> StandInRecord | None:
        """The stand-in recorded for *rom_id* — where it goes, whether or not it is there now — or ``None``."""
        record = (await self._loop.run_in_executor(None, self._index.read)).get(rom_id)
        return record if record is not None and record.path is not None else None

    async def clear_for_download(self, rom_id: int) -> bool:
        """Remove *rom_id*'s stand-in so its download finds its path free; whether one was removed.

        The record stays, so :meth:`restore` can put it back. A stand-in that
        cannot be removed is logged and answers ``False``.
        """
        async with self._lock:
            return await self._loop.run_in_executor(None, self._clear_io, rom_id)

    async def restore(self, rom_id: int) -> bool:
        """Put *rom_id*'s recorded stand-in back once no download holds it; whether it stands there now.

        Puts nothing back while the library surface writes no ES-DE, while the
        game has an install record, where nothing is recorded, or where
        something else now holds the path. A failure is logged and answers
        ``False``.
        """
        if not writes_esde(library_surface(self._settings.get("library_surface"))):
            return False
        if rom_id in await self._loop.run_in_executor(None, self._installed_rom_ids_io):
            return False
        async with self._lock:
            return await self._loop.run_in_executor(None, self._restore_io, rom_id)

    async def remove_all(self) -> int:
        """Remove every stand-in Tender placed and empty the record, for a surface that stops writing ES-DE.

        Answers how many were removed.
        """
        async with self._lock:
            return await self._loop.run_in_executor(None, self._remove_all_io)

    # ── Blocking work, run off the loop ─────────────────────────────

    def _installed_rom_ids_io(self) -> frozenset[int]:
        with self._uow_factory() as uow:
            return frozenset(install.rom_id for install in uow.rom_installs.iter_all())

    def _decide_io(self, roms: list[dict[str, Any]], recorded: Mapping[int, StandInRecord]) -> list[_Decision]:
        """Each of *roms*' stand-in, asked the download's way: its system, its folder, and the shape it takes there."""
        by_platform: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for rom in roms:
            by_platform[str(rom.get("platform_slug") or "")].append(rom)
        decisions: list[_Decision] = []
        for slug, group in by_platform.items():
            system = self._platform_systems.platform_system(slug, source=RETRODECK).taken
            if system is None:
                self._log_debug(f"[esde] no stand-ins for {slug!r}: RetroDECK has no system for it")
                continue
            folder = self._retrodeck_folders.download_folder(system)
            if isinstance(folder, FolderRefused):
                self._log_debug(f"[esde] no stand-ins for {slug!r}: {folder.message}")
                continue
            m3u_supported = self._m3u_support(system)
            for rom in group:
                record = self._record_io(rom, slug, system, folder, m3u_supported, recorded.get(int(rom["id"])))
                if record is not None:
                    decisions.append(_Decision(rom_id=int(rom["id"]), record=record))
        return decisions

    def _record_io(
        self,
        rom: dict[str, Any],
        slug: str,
        system: str,
        folder: str,
        m3u_supported: bool,
        recorded: StandInRecord | None,
    ) -> StandInRecord | None:
        """*rom*'s stand-in in *folder*: its recorded shape while RomM has not changed it, else the one predicted.

        A row the sync rebuilt from its own record carries no ``updated_at``, and
        reuses the recorded shape. ``None`` where RomM's detail could not be read.
        """
        rom_id = int(rom["id"])
        updated_at = rom.get("updated_at")
        if recorded is not None and recorded.system == system and updated_at in (None, recorded.updated_at):
            shape, updated_at = recorded.shape, recorded.updated_at
        elif listing_settles_shape(rom):
            shape = predict_stand_in(rom, m3u_supported=m3u_supported)
        else:
            try:
                detail = self._romm_api.get_rom(rom_id)
            except Exception as e:
                self._logger.warning(f"[esde] no stand-in for ROM {rom_id} this run: its detail was not read: {e}")
                return None
            shape = predict_stand_in(detail, m3u_supported=m3u_supported)
            updated_at = updated_at or detail.get("updated_at")
        path = None
        if shape is not None:
            # Joined, never ``safe_join``ed: the path names the link itself, and
            # resolving it would answer the stub the link points at.
            try:
                path = os.path.join(folder, safe_path_component(shape.name))
            except PathTraversalError:
                shape = None
        return StandInRecord(platform_slug=slug, system=system, updated_at=updated_at, path=path, shape=shape)

    def _place_io(self, decisions: list[_Decision]) -> set[str]:
        """Record *decisions* and place each stand-in they name; answer every folder one was placed into or left."""
        index = self._index.read()
        folders: set[str] = set()
        for decision in decisions:
            earlier = index.get(decision.rom_id)
            if earlier is not None and earlier.path is not None:
                folders.add(os.path.dirname(earlier.path))
            index[decision.rom_id] = decision.record
            path, shape = decision.record.path, decision.record.shape
            if path is None or shape is None:
                continue
            folders.add(os.path.dirname(path))
            try:
                outcome = self._stand_ins.place(decision.rom_id, shape, path)
            except (OSError, ValueError) as e:
                self._logger.warning(f"[esde] the stand-in for ROM {decision.rom_id} at {path} was not placed: {e}")
                continue
            if outcome == OCCUPIED:
                self._log_debug(f"[esde] no stand-in for ROM {decision.rom_id}: something else is at {path}")
        self._write_io(index)
        return folders

    def _settle_io(self, run: _Run, installed: frozenset[int]) -> int:
        """Remove every stand-in the run does not keep, and keep the record of those it does; answer how many went."""
        index = self._index.read()
        scope = run.scope()
        kept = {rom_id: record for rom_id, record in index.items() if scope.keeps(rom_id, record)}
        wanted = wanted_links(kept, installed)
        removed = 0
        for rom_id, record in index.items():
            if record.path is not None and wanted.get(record.path) != rom_id:
                removed += self._remove_io(rom_id, record.path)
        unrecorded: set[int] = set()
        for folder in run.swept_folders:
            for path, rom_id in self._stand_ins.list_in(folder).items():
                if wanted.get(path) != rom_id:
                    removed += self._remove_io(rom_id, path)
                    unrecorded.add(rom_id)
        for rom_id in sorted((set(index) | unrecorded) - set(kept)):
            self._remove_stub_io(rom_id)
        self._write_io(kept)
        return removed

    def _is_stand_in_io(self, path: str) -> bool:
        return (
            self._stand_ins.stand_in_at(path) is not None
            or self._stand_ins.stand_in_at(os.path.dirname(path)) is not None
        )

    def _clear_io(self, rom_id: int) -> bool:
        record = self._index.read().get(rom_id)
        return record is not None and record.path is not None and bool(self._remove_io(rom_id, record.path))

    def _restore_io(self, rom_id: int) -> bool:
        record = self._index.read().get(rom_id)
        if record is None or record.path is None or record.shape is None:
            return False
        try:
            return self._stand_ins.place(rom_id, record.shape, record.path) != OCCUPIED
        except (OSError, ValueError) as e:
            self._logger.warning(f"[esde] the stand-in for ROM {rom_id} at {record.path} was not put back: {e}")
            return False

    def _remove_all_io(self) -> int:
        index = self._index.read()
        removed = 0
        for rom_id, record in index.items():
            if record.path is not None:
                removed += self._remove_io(rom_id, record.path)
        found: set[int] = set()
        for folder in {os.path.dirname(record.path) for record in index.values() if record.path is not None}:
            for path, rom_id in self._stand_ins.list_in(folder).items():
                removed += self._remove_io(rom_id, path)
                found.add(rom_id)
        for rom_id in sorted(set(index) | found):
            self._remove_stub_io(rom_id)
        self._write_io({})
        return removed

    def _remove_io(self, rom_id: int, path: str) -> int:
        """Remove *rom_id*'s stand-in at *path*: 1 where one went, 0 where none was there or it could not go."""
        try:
            return int(self._stand_ins.remove(rom_id, path))
        except OSError as e:
            self._logger.warning(f"[esde] the stand-in for ROM {rom_id} at {path} was not removed: {e}")
            return 0

    def _remove_stub_io(self, rom_id: int) -> None:
        try:
            self._stand_ins.remove_stub(rom_id)
        except OSError as e:
            self._logger.warning(f"[esde] the stub of ROM {rom_id}'s stand-in was not removed: {e}")

    def _write_io(self, index: Mapping[int, StandInRecord]) -> None:
        """Write *index*; a record that cannot be written is logged, its stand-ins recognised by their targets."""
        try:
            self._index.write(index)
        except OSError as e:
            self._logger.warning(f"[esde] the record of stand-ins was not written: {e}")
