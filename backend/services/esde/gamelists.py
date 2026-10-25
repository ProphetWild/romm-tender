"""ES-DE's gamelists: RomM's metadata merged into the gamelist of each system the library sync lists games in.

How Tender's fields are merged into a gamelist that ES-DE, its scraper and the
user also write is ``domain/esde_gamelist.py``'s; a game's fields are
``domain/esde_gamelist_fields.py``'s; the record of what waits and what was
written, and the path an entry carries, are ``domain/esde_gamelist_record.py``'s;
reading and writing the files is the ``EsdeGamelistStore``'s. A game's entry is
the one ES-DE lists it as — its stand-in, or what its download left — which
the stand-in service answers (``EsdeLibraryPass`` hands it on).

The games of a unit the sync read wait in their system's record; nothing is
written into a gamelist while a sync runs. A gamelist is written only while no
ES-DE runs (docs/specs/esde-library.md, §4, D16): at the end of a run when
none does, and otherwise from ES-DE's ``startup`` event, which it fires before
it reads any gamelist and waits on. What waits is kept in the record, so it
outlives a restart of this backend. A gamelist that cannot be read as one is
left as it is, and what waits for it keeps waiting.

Every file read and write runs off the loop. The record is read once, written
whenever it changes, and changed under one lock, so a run's end and ES-DE's
start never interleave their writes.
"""

from __future__ import annotations

import asyncio
import os
from collections import defaultdict
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any

from domain.esde_gamelist import UnreadableGamelist, merge_gamelist
from domain.esde_gamelist_fields import gamelist_fields
from domain.esde_gamelist_record import SystemGamelist, gamelist_entry_path, gamelist_file, is_path_component
from domain.library_surface import writes_esde
from domain.retrodeck_folders import FolderRefused

if TYPE_CHECKING:
    import logging
    from collections.abc import Collection, Mapping

    from domain.esde_stand_in import EsdeEntry
    from domain.library_surface import LibrarySurface
    from services.protocols import (
        DebugLogger,
        EsdeGamelistIndexStore,
        EsdeGamelistStore,
        EsdeRunningFn,
        RetroDeckFolders,
    )


@dataclass(frozen=True)
class EsdeGamelistServiceConfig:
    """Frozen wiring bundle handed to ``EsdeGamelistService.__init__``.

    ``files`` reads and writes the gamelists and ``index`` keeps the record of
    them. ``retrodeck_folders`` answers where ES-DE keeps its gamelists, and
    ``esde_running`` whether an ES-DE runs now.
    """

    files: EsdeGamelistStore
    index: EsdeGamelistIndexStore
    retrodeck_folders: RetroDeckFolders
    esde_running: EsdeRunningFn
    loop: asyncio.AbstractEventLoop
    logger: logging.Logger
    log_debug: DebugLogger


class EsdeGamelistService:
    """Writes RomM's metadata into ES-DE's gamelists for the games the library sync lists there.

    Implements the ``EsdePendingGamelists`` Protocol ES-DE's ``startup`` hook
    reaches.
    """

    def __init__(self, *, config: EsdeGamelistServiceConfig) -> None:
        self._files = config.files
        self._index = config.index
        self._retrodeck_folders = config.retrodeck_folders
        self._esde_running = config.esde_running
        self._loop = config.loop
        self._logger = config.logger
        self._log_debug = config.log_debug
        self._lock = asyncio.Lock()
        self._record: dict[str, SystemGamelist] | None = None

    async def collect(self, entries: Collection[EsdeEntry], roms: Mapping[int, dict[str, Any]]) -> None:
        """Leave each of *entries*' fields, read off its RomM ROM in *roms*, waiting in its system's gamelist.

        An entry whose ROM *roms* does not hold adds nothing, nor does one
        whose name ES-DE reads back from a gamelist as no one entry, or whose
        system names no one folder. A game already waiting under the same path
        waits with its newer fields. Nothing is written into a gamelist.
        """
        added: dict[str, dict[str, dict[str, str]]] = defaultdict(dict)
        for entry in entries:
            rom = roms.get(entry.rom_id)
            if rom is None:
                continue
            path = gamelist_entry_path(os.path.basename(entry.path))
            if path is None or not is_path_component(entry.system):
                self._log_debug(
                    f"[esde] no gamelist entry for ROM {entry.rom_id}: ES-DE reads none back as {entry.path!r}"
                    f" in {entry.system!r}"
                )
                continue
            added[entry.system][path] = gamelist_fields(rom)
        if not added:
            return
        async with self._lock:
            record = await self._read_record()
            for system, pending in added.items():
                current = record.get(system, SystemGamelist(pending={}, written={}))
                record[system] = replace(current, pending={**current.pending, **pending})
            await self._write_record(record)

    async def settle(self) -> None:
        """Write every gamelist with entries waiting, unless an ES-DE runs: then they wait for its next start."""
        async with self._lock:
            record = await self._read_record()
            if not _anything_waits(record):
                return
            if await self._loop.run_in_executor(None, self._esde_running):
                self._log_debug("[esde] ES-DE is running, so the gamelists wait for its next start")
                return
            await self._write_waiting(record)

    async def write_pending(self) -> None:
        """Write every gamelist with entries waiting, whether or not an ES-DE runs: ES-DE's ``startup`` event's call."""
        async with self._lock:
            record = await self._read_record()
            if _anything_waits(record):
                await self._write_waiting(record)

    async def surface_stored(self, surface: LibrarySurface) -> None:
        """Once the library surface stored is *surface* and writes no ES-DE, drop every entry still waiting.

        Every gamelist stays as it is, the fields Tender wrote into it
        included, and so does the record of what Tender wrote: should ES-DE be
        chosen again, a field still holding Tender's value is Tender's to
        update and one changed since is the user's.
        """
        if writes_esde(surface):
            return
        async with self._lock:
            record = await self._read_record()
            if not _anything_waits(record):
                return
            for system, gamelist in list(record.items()):
                record[system] = replace(gamelist, pending={})
            await self._write_record(record)
        self._log_debug("[esde] leaving ES-DE dropped the gamelist entries still waiting")

    async def _write_waiting(self, record: dict[str, SystemGamelist]) -> None:
        """Write the gamelist of every system in *record* with entries waiting, and record what each answered."""
        folders = await self._loop.run_in_executor(None, self._retrodeck_folders.esde_folders)
        if isinstance(folders, FolderRefused):
            self._log_debug(f"[esde] the gamelists wait: {folders.message}")
            return
        settled = await self._loop.run_in_executor(None, self._write_io, dict(record), folders.gamelists)
        if settled:
            record.update(settled)
            await self._write_record(record)

    async def _read_record(self) -> dict[str, SystemGamelist]:
        record = self._record
        if record is None:
            record = await self._loop.run_in_executor(None, self._index.read)
            self._record = record
        return record

    async def _write_record(self, record: Mapping[str, SystemGamelist]) -> None:
        """Write *record*; one that cannot be written is logged, and what it holds is written with the next change."""
        try:
            await self._loop.run_in_executor(None, self._index.write, dict(record))
        except OSError as e:
            self._logger.warning(f"[esde] the record of gamelists was not written: {e}")

    # ── Blocking work, run off the loop ─────────────────────────────

    def _write_io(self, record: Mapping[str, SystemGamelist], gamelists: str) -> dict[str, SystemGamelist]:
        """Each system of *record* whose gamelist now holds what waited for it, as it stands once written."""
        settled: dict[str, SystemGamelist] = {}
        for system, gamelist in sorted(record.items()):
            if gamelist.pending:
                written = self._write_system_io(system, gamelist, gamelists)
                if written is not None:
                    settled[system] = written
        return settled

    def _write_system_io(self, system: str, gamelist: SystemGamelist, gamelists: str) -> SystemGamelist | None:
        """Merge what waits for *system* into its gamelist, written only where that changes it; ``None`` if not."""
        waiting = len(gamelist.pending)
        path = gamelist_file(gamelists, system)
        try:
            current = self._files.read_gamelist(path)
        except (OSError, ValueError) as e:
            self._logger.warning(f"[esde] {path} could not be read, so {waiting} entries wait: {e!r}")
            return None
        merged = merge_gamelist(current, gamelist.pending, gamelist.written)
        if isinstance(merged, UnreadableGamelist):
            self._logger.warning(f"[esde] {path} is left as it is, since {merged.reason}; {waiting} entries wait")
            return None
        if merged.changed:
            try:
                self._files.write_gamelist(path, merged.text)
            except (OSError, ValueError) as e:
                self._logger.warning(f"[esde] {path} could not be written, so {waiting} entries wait: {e!r}")
                return None
        self._log_debug(f"[esde] {path} holds Tender's fields for {waiting} entries")
        return SystemGamelist(pending={}, written=merged.written)


def _anything_waits(record: Mapping[str, SystemGamelist]) -> bool:
    return any(gamelist.pending for gamelist in record.values())
