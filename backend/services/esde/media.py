"""ES-DE's media: RomM's files linked into ES-DE's media tree for each game the library sync lists there.

Which RomM file fills which folder, and the name ES-DE looks for it under, is
``domain/esde_media.py``'s; the record of what Tender fetched and linked is
``domain/esde_media_record.py``'s; every file operation is the
``EsdeMediaStore``'s. A game's media are named after the entry ES-DE lists it
as — its stand-in, or what its download left — which the stand-in service
answers (``EsdeLibraryPass`` hands both on).

The images are fetched at sync time, as background work a few fetches at a
time, so the sync's progress never waits for them. A file is fetched only where
its fingerprint (its RomM path and the ROM's ``updated_at``) moved since it was,
or its copy is gone, so an unchanged library costs no request; a kept copy of
the same file is revalidated rather than fetched whole. A file RomM does not
serve, or whose bytes ES-DE does not read, leaves its slot empty. Videos and
manuals are fetched when ES-DE selects the game (docs/specs/esde-library.md,
D4) into a cache capped at :data:`ON_DEMAND_CAP_BYTES`; the copies of the games
selected longest ago go first, each link before its file.

The record is read once and then kept in memory, and written once the queue
runs dry and after each selection, run's end and switch of the surface: a
first fill over a whole library would otherwise rewrite the whole file once
per game. Tender's links are known by their target, so a link the record lost
is replaced or removed like any other.

Nothing is fetched while the library surface writes no ES-DE: a sync's games
arrive only through a pass whose run writes ES-DE, and a selection reads the
setting. Every file operation and every RomM request runs off the loop. The
record, and the files a game's links name, change under one lock; a fetch
begun before its game was removed, or before ES-DE was left, links nothing.
"""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any

from domain.esde_media import (
    MEDIA_FOLDERS,
    ON_SELECTION_FOLDERS,
    SYNC_FOLDERS,
    extension_for,
    lookup_order,
    media_fingerprint,
    media_link_path,
    media_sources,
    media_stem,
)
from domain.esde_media_record import (
    HELD,
    FolderMedia,
    GameMedia,
    is_current,
    on_demand_evictions,
    revalidates,
    with_source,
)
from domain.library_surface import library_surface, writes_esde
from domain.retrodeck_folders import FolderRefused
from lib.background_tasks import BackgroundTasks

if TYPE_CHECKING:
    import logging
    from collections.abc import Collection, Mapping, Sequence

    from domain.esde_media import MediaFolder
    from domain.esde_stand_in import EsdeEntry, SyncedScope
    from domain.library_surface import LibrarySurface
    from models.cover import CoverRevalidation
    from services.protocols import (
        Clock,
        DebugLogger,
        EsdeMediaIndexStore,
        EsdeMediaStore,
        RetroDeckFolders,
        RommMediaReader,
    )

# The on-demand cache's cap (docs/specs/esde-library.md, §3d).
ON_DEMAND_CAP_BYTES = 8 * 1024**3
_CONCURRENT_FETCHES = 3


@dataclass(frozen=True)
class EsdeMediaServiceConfig:
    """Frozen wiring bundle handed to ``EsdeMediaService.__init__``.

    ``romm_api`` fetches RomM's media files, ``files`` keeps their copies and
    links them, and ``index`` keeps the record of both. ``retrodeck_folders``
    answers ES-DE's media tree. ``settings`` is the live dict a selection reads
    the library surface from, and ``clock`` stamps a selection.
    ``on_demand_cap_bytes`` caps the copies fetched on selection.
    """

    romm_api: RommMediaReader
    files: EsdeMediaStore
    index: EsdeMediaIndexStore
    retrodeck_folders: RetroDeckFolders
    settings: dict[str, Any]
    clock: Clock
    loop: asyncio.AbstractEventLoop
    logger: logging.Logger
    log_debug: DebugLogger
    on_demand_cap_bytes: int = ON_DEMAND_CAP_BYTES


@dataclass(frozen=True, slots=True)
class _Job:
    """One game to bring up to date: its entry and, from a unit the sync read, its RomM ROM.

    *epoch* and *removals* are what they were when it was queued, so a job
    that outlived its game's removal, or ES-DE being left, is known.
    """

    entry: EsdeEntry
    rom: Mapping[str, Any] | None
    media_root: str
    epoch: int
    removals: int


@dataclass(frozen=True, slots=True)
class _Fetch:
    folder: MediaFolder
    server_path: str
    fingerprint: str
    etag: str | None
    last_modified: str | None


@dataclass(frozen=True, slots=True)
class _Fetched:
    """What one fetch brought: RomM's answer (``None`` where it served no file), and the bytes kept, with their kind."""

    fetch: _Fetch
    answer: CoverRevalidation | None
    staged: str | None
    extension: str | None


class EsdeMediaService:
    """Links RomM's media into ES-DE for the games the library sync lists there, and their videos on selection.

    Implements the ``EsdeOnDemandMedia`` Protocol ES-DE's ``game-select`` hook
    reaches.
    """

    def __init__(self, *, config: EsdeMediaServiceConfig) -> None:
        self._romm_api = config.romm_api
        self._files = config.files
        self._index = config.index
        self._retrodeck_folders = config.retrodeck_folders
        self._settings = config.settings
        self._clock = config.clock
        self._loop = config.loop
        self._logger = config.logger
        self._log_debug = config.log_debug
        self._cap = config.on_demand_cap_bytes
        self._tasks = BackgroundTasks(logger=config.logger)
        self._lock = asyncio.Lock()
        self._loading = asyncio.Lock()
        self._record: dict[int, GameMedia] | None = None
        self._dirty = False
        self._queue: dict[int, _Job] = {}
        self._running: dict[int, _Job] = {}
        self._workers = 0
        self._selecting: set[int] = set()
        self._epoch = 0
        self._removals: dict[int, int] = {}

    # ── The library sync's games ────────────────────────────────────

    async def queue(self, entries: Collection[EsdeEntry], roms: Mapping[int, Mapping[str, Any]]) -> None:
        """Bring each of *entries*' games up to date in the background; answers once they are queued.

        A game whose RomM ROM *roms* holds has its media fetched where they
        changed, dropped where RomM no longer has them, and linked under its
        entry's stem; one without is only moved to its entry's stem, nothing
        fetched or dropped. A game already queued stays queued once, as the
        newer entry says, keeping the ROM the earlier one carried where the
        newer one has none. Nothing is queued where ES-DE's media tree is
        refused.
        """
        if not entries:
            return
        folders = await self._loop.run_in_executor(None, self._retrodeck_folders.esde_folders)
        if isinstance(folders, FolderRefused):
            self._log_debug(f"[esde] no media for {len(entries)} games: {folders.message}")
            return
        for entry in entries:
            rom = roms.get(entry.rom_id)
            earlier = self._queue.get(entry.rom_id)
            if rom is None and earlier is not None:
                rom = earlier.rom
            self._queue[entry.rom_id] = _Job(
                entry=entry,
                rom=rom,
                media_root=folders.downloaded_media,
                epoch=self._epoch,
                removals=self._removals.get(entry.rom_id, 0),
            )
        self._start_workers()

    async def settle(self, scope: SyncedScope) -> None:
        """Remove the media of every game *scope* no longer syncs: its links, and the copies they point at.

        A game still queued, or being brought up to date, is dropped too.
        """
        async with self._lock:
            record = await self._read_record()
            gone = {rom_id: game for rom_id, game in record.items() if not scope.keeps(rom_id, game.platform_slug)}
            pending = [*self._queue.values(), *self._running.values()]
            dropped = {
                job.entry.rom_id for job in pending if not scope.keeps(job.entry.rom_id, job.entry.platform_slug)
            }
            for rom_id in set(gone) | dropped:
                self._removals[rom_id] = self._removals.get(rom_id, 0) + 1
                self._queue.pop(rom_id, None)
            for rom_id in gone:
                del record[rom_id]
            if gone:
                self._dirty = True
                await self._loop.run_in_executor(None, self._remove_io, gone)
        if gone:
            self._log_debug(f"[esde] the run's end removed the media of {len(gone)} games")
        await self._flush()

    async def surface_stored(self, surface: LibrarySurface) -> None:
        """Once the library surface stored is *surface* and writes no ES-DE, remove every link and copy of Tender's.

        Every link of Tender's in ES-DE's media tree goes, and nothing else
        there; what is queued is dropped, and a fetch under way links nothing.
        """
        if writes_esde(surface):
            return
        self._epoch += 1
        self._queue.clear()
        folders = await self._loop.run_in_executor(None, self._retrodeck_folders.esde_folders)
        media_root = None if isinstance(folders, FolderRefused) else folders.downloaded_media
        async with self._lock:
            record = await self._read_record()
            removed = await self._loop.run_in_executor(None, self._leave_io, dict(record), media_root)
            record.clear()
            self._dirty = True
        await self._flush()
        self._log_debug(f"[esde] leaving ES-DE removed {removed} media links")

    async def shutdown(self) -> None:
        """Stop the work under way and write what the record holds."""
        self._queue.clear()
        await self._tasks.shutdown()
        await self._flush()

    # ── A game ES-DE selected ───────────────────────────────────────

    def fetch_on_selection(self, rom_id: int) -> None:
        """Fetch and link *rom_id*'s video and manual in the background, and answer at once.

        Nothing is fetched while the library surface writes no ES-DE, for a
        game no sync recorded media for, or for a copy still current; a game
        selected again while its fetch is under way is that same fetch. Each
        selection stamps the game, and then the cache's cap evicts the copies
        of the games selected longest ago.
        """
        if rom_id in self._selecting:
            return
        self._selecting.add(rom_id)
        self._tasks.spawn(self._select(rom_id), f"esde_media_select_{rom_id}")

    async def _select(self, rom_id: int) -> None:
        try:
            if not writes_esde(library_surface(self._settings.get("library_surface"))):
                return
            folders = await self._loop.run_in_executor(None, self._retrodeck_folders.esde_folders)
            if isinstance(folders, FolderRefused):
                self._log_debug(f"[esde] no video or manual for ROM {rom_id}: {folders.message}")
                return
            game = (await self._read_record()).get(rom_id)
            if game is None:
                self._log_debug(f"[esde] no video or manual for ROM {rom_id}: no sync recorded its media")
                return
            epoch, removals = self._epoch, self._removals.get(rom_id, 0)
            fetches = await self._loop.run_in_executor(None, self._plan_selection_io, rom_id, game)
            fetched = await self._fetch_all(rom_id, fetches, on_demand=True)
            async with self._lock:
                current = (await self._read_record()).get(rom_id)
                if current is None or (epoch, removals) != (self._epoch, self._removals.get(rom_id, 0)):
                    await self._loop.run_in_executor(None, self._discard_io, fetched)
                    return
                record = await self._read_record()
                record[rom_id] = await self._loop.run_in_executor(
                    None, self._commit_selection_io, rom_id, current, folders.downloaded_media, fetched
                )
                await self._evict(record)
                self._dirty = True
            await self._flush()
        finally:
            self._selecting.discard(rom_id)

    # ── The queue ───────────────────────────────────────────────────

    def _start_workers(self) -> None:
        free = sum(1 for rom_id in self._queue if rom_id not in self._running)
        while self._workers < _CONCURRENT_FETCHES and free > 0:
            self._workers += 1
            free -= 1
            self._tasks.spawn(self._work(), "esde_media")

    def _next_job(self) -> _Job | None:
        """The first job queued for a game no worker holds, taken off the queue."""
        for rom_id in self._queue:
            if rom_id not in self._running:
                return self._queue.pop(rom_id)
        return None

    async def _work(self) -> None:
        """Bring the games queued up to date one after another, and write the record once none is left."""
        try:
            while (job := self._next_job()) is not None:
                rom_id = job.entry.rom_id
                self._running[rom_id] = job
                try:
                    await self._bring_up_to_date(job)
                except Exception as e:
                    self._logger.warning(f"[esde] the media of ROM {rom_id} were not brought up to date: {e!r}")
                finally:
                    del self._running[rom_id]
        finally:
            self._workers -= 1
        if self._workers == 0:
            await self._flush()

    async def _bring_up_to_date(self, job: _Job) -> None:
        entry = job.entry
        try:
            stem = media_stem(os.path.basename(entry.path), is_directory=entry.is_directory)
        except ValueError:
            self._log_debug(f"[esde] no media for ROM {entry.rom_id}: {entry.path!r} names no entry")
            return
        game = (await self._read_record()).get(entry.rom_id)
        sources: dict[MediaFolder, str] | None = None
        fetched: dict[MediaFolder, _Fetched] = {}
        if job.rom is None:
            if game is None or (game.stem, game.system) == (stem, entry.system):
                return
            updated_at = game.updated_at
        else:
            sources = media_sources(job.rom)
            raw = job.rom.get("updated_at")
            updated_at = raw if isinstance(raw, str) else None
            fetches = await self._loop.run_in_executor(None, self._plan_io, entry.rom_id, game, sources, updated_at)
            fetched = await self._fetch_all(entry.rom_id, fetches, on_demand=False)
        async with self._lock:
            record = await self._read_record()
            if (job.epoch, job.removals) != (self._epoch, self._removals.get(entry.rom_id, 0)):
                await self._loop.run_in_executor(None, self._discard_io, fetched)
                return
            record[entry.rom_id] = await self._loop.run_in_executor(
                None, self._commit_io, job, stem, record.get(entry.rom_id), sources, updated_at, fetched
            )
            self._dirty = True

    async def _fetch_all(self, rom_id: int, fetches: list[_Fetch], *, on_demand: bool) -> dict[MediaFolder, _Fetched]:
        """Fetch each of *fetches* in turn; one that fails is left out, and the failures are logged once."""
        fetched: dict[MediaFolder, _Fetched] = {}
        failures: list[str] = []
        for fetch in fetches:
            try:
                fetched[fetch.folder] = await self._loop.run_in_executor(None, self._fetch_io, rom_id, fetch, on_demand)
            except Exception as e:
                failures.append(f"{fetch.folder}: {e!r}")
        if failures:
            self._logger.warning(
                f"[esde] {len(failures)} media files of ROM {rom_id} were not fetched and are asked for again "
                f"next time: {'; '.join(failures)}"
            )
        return fetched

    async def _evict(self, record: dict[int, GameMedia]) -> None:
        """Evict the on-demand copies over the cap from *record*, each link before its copy."""
        evicted = on_demand_evictions(record, self._cap)
        if not evicted:
            return
        doomed: list[tuple[int, MediaFolder, FolderMedia]] = [
            (rom_id, folder, record[rom_id].folders[folder]) for rom_id, folder in evicted
        ]
        await self._loop.run_in_executor(None, self._evict_io, doomed)
        for rom_id, folder, media in doomed:
            game = record[rom_id]
            emptied = replace(
                media, fingerprint=None, etag=None, last_modified=None, extension=None, link_path=None, size_bytes=None
            )
            record[rom_id] = replace(game, folders={**game.folders, folder: emptied})
        self._log_debug(f"[esde] the on-demand cache's cap evicted {len(evicted)} files")

    async def _read_record(self) -> dict[int, GameMedia]:
        async with self._loading:
            record = self._record
            if record is None:
                record = await self._loop.run_in_executor(None, self._index.read)
                self._record = record
            return record

    async def _flush(self) -> None:
        """Write the record if it changed since it was last written; a write that fails is logged and tried later."""
        async with self._lock:
            if not self._dirty or self._record is None:
                return
            self._dirty = False
            try:
                await self._loop.run_in_executor(None, self._index.write, dict(self._record))
            except OSError as e:
                self._dirty = True
                self._logger.warning(f"[esde] the record of media links was not written: {e}")

    # ── Blocking work, run off the loop ─────────────────────────────

    def _discard_io(self, fetched: Mapping[MediaFolder, _Fetched]) -> None:
        for outcome in fetched.values():
            if outcome.staged is not None:
                self._files.discard(outcome.staged)

    def _plan_io(
        self, rom_id: int, game: GameMedia | None, sources: Mapping[MediaFolder, str], updated_at: str | None
    ) -> list[_Fetch]:
        """The fetches *rom_id*'s synced folders need: each whose file moved, or whose copy is gone."""
        fetches: list[_Fetch] = []
        for folder in SYNC_FOLDERS:
            server_path = sources.get(folder)
            if server_path is None:
                continue
            media = None if game is None else game.folders.get(folder)
            fetch = self._fetch_for_io(rom_id, folder, media, server_path, updated_at, on_demand=False)
            if fetch is not None:
                fetches.append(fetch)
        return fetches

    def _plan_selection_io(self, rom_id: int, game: GameMedia) -> list[_Fetch]:
        """The fetches the folders *game* fetches on selection need."""
        fetches: list[_Fetch] = []
        for folder in ON_SELECTION_FOLDERS:
            media = game.folders.get(folder)
            if media is None:
                continue
            fetch = self._fetch_for_io(rom_id, folder, media, media.server_path, game.updated_at, on_demand=True)
            if fetch is not None:
                fetches.append(fetch)
        return fetches

    def _fetch_for_io(
        self,
        rom_id: int,
        folder: MediaFolder,
        media: FolderMedia | None,
        server_path: str,
        updated_at: str | None,
        *,
        on_demand: bool,
    ) -> _Fetch | None:
        fingerprint = media_fingerprint(server_path, updated_at)
        cached = self._is_kept_io(rom_id, folder, media, on_demand=on_demand)
        if is_current(media, fingerprint, cached=cached):
            return None
        if media is not None and revalidates(media, server_path, cached=cached):
            return _Fetch(folder, server_path, fingerprint, media.etag, media.last_modified)
        return _Fetch(folder, server_path, fingerprint, None, None)

    def _is_kept_io(self, rom_id: int, folder: MediaFolder, media: FolderMedia | None, *, on_demand: bool) -> bool:
        if media is None or media.extension is None:
            return False
        return self._files.kept_size(rom_id, folder, media.extension, on_demand=on_demand) is not None

    def _fetch_io(self, rom_id: int, fetch: _Fetch, on_demand: bool) -> _Fetched:
        """Fetch *fetch* into a staged file, kept only where it holds bytes ES-DE reads in its folder."""
        staged = self._files.stage(rom_id, fetch.folder, on_demand=on_demand)
        try:
            answer = self._romm_api.download_media(
                fetch.server_path, staged, etag=fetch.etag, last_modified=fetch.last_modified
            )
            fresh = answer is not None and not answer.not_modified
            extension = extension_for(fetch.folder, self._files.head(staged)) if fresh else None
        except BaseException:
            self._files.discard(staged)
            raise
        if extension is None:
            self._files.discard(staged)
            return _Fetched(fetch, answer, None, None)
        return _Fetched(fetch, answer, staged, extension)

    def _commit_io(
        self,
        job: _Job,
        stem: str,
        current: GameMedia | None,
        sources: Mapping[MediaFolder, str] | None,
        updated_at: str | None,
        fetched: Mapping[MediaFolder, _Fetched],
    ) -> GameMedia:
        """*job*'s game as it stands once its fetched files are kept and every folder is linked under *stem*.

        *sources* are the folders RomM has a file for, ``None`` where the game
        only moves to *stem*; a folder RomM has none for any more is unlinked
        and its copy forgotten.
        """
        rom_id = job.entry.rom_id
        recorded = {} if current is None else current.folders
        folders: dict[MediaFolder, FolderMedia] = {}
        for folder in MEDIA_FOLDERS:
            media = recorded.get(folder)
            if sources is not None and folder not in sources:
                if media is not None:
                    self._drop_io(rom_id, folder, media)
                continue
            if folder in ON_SELECTION_FOLDERS:
                media = media if sources is None else with_source(media, sources[folder])
            elif folder in fetched:
                media = self._apply_io(rom_id, media, fetched[folder])
            if media is not None:
                folders[folder] = self._link_io(rom_id, folder, media, job.media_root, job.entry.system, stem)
        return GameMedia(
            platform_slug=job.entry.platform_slug,
            system=job.entry.system,
            stem=stem,
            updated_at=updated_at,
            folders=folders,
        )

    def _commit_selection_io(
        self, rom_id: int, game: GameMedia, media_root: str, fetched: Mapping[MediaFolder, _Fetched]
    ) -> GameMedia:
        """*game* once what a selection fetched is kept and linked, each folder fetched on selection stamped."""
        selected_at = self._clock.time()
        folders = dict(game.folders)
        for folder in ON_SELECTION_FOLDERS:
            media = folders.get(folder)
            if media is None:
                continue
            outcome = fetched.get(folder)
            if outcome is not None and outcome.fetch.server_path == media.server_path:
                media = self._apply_io(rom_id, media, outcome)
            elif outcome is not None:
                self._discard_io({folder: outcome})
            media = self._link_io(rom_id, folder, media, media_root, game.system, game.stem)
            folders[folder] = replace(media, last_selected=selected_at)
        return replace(game, folders=folders)

    def _apply_io(self, rom_id: int, media: FolderMedia | None, outcome: _Fetched) -> FolderMedia:
        """*media* once *outcome* is kept: the new copy, the same one revalidated, or none for an empty slot."""
        fetch, answer = outcome.fetch, outcome.answer
        on_demand = fetch.folder in ON_SELECTION_FOLDERS
        selected = None if media is None else media.last_selected
        if answer is not None and answer.not_modified and media is not None:
            return replace(
                media,
                server_path=fetch.server_path,
                fingerprint=fetch.fingerprint,
                etag=answer.etag or media.etag,
                last_modified=answer.last_modified or media.last_modified,
            )
        empty = FolderMedia(
            server_path=fetch.server_path,
            fingerprint=fetch.fingerprint,
            etag=None,
            last_modified=None,
            extension=None,
            link_path=None,
            last_selected=selected,
        )
        if answer is None or outcome.staged is None or outcome.extension is None:
            if media is not None:
                self._drop_io(rom_id, fetch.folder, media)
            return empty
        try:
            size = self._files.keep(outcome.staged, rom_id, fetch.folder, outcome.extension, on_demand=on_demand)
        except OSError as e:
            self._files.discard(outcome.staged)
            self._logger.warning(f"[esde] the {fetch.folder} file of ROM {rom_id} was not kept: {e}")
            return media if media is not None else replace(empty, fingerprint=None)
        return replace(
            empty,
            etag=answer.etag,
            last_modified=answer.last_modified,
            extension=outcome.extension,
            link_path=None if media is None else media.link_path,
            size_bytes=size if on_demand else None,
        )

    def _link_io(
        self, rom_id: int, folder: MediaFolder, media: FolderMedia, media_root: str, system: str, stem: str
    ) -> FolderMedia:
        """*media* with its kept copy linked where ES-DE looks for *stem*'s *folder* file, or unlinked where none is."""
        on_demand = folder in ON_SELECTION_FOLDERS
        try:
            if media.extension is None or not self._is_kept_io(rom_id, folder, media, on_demand=on_demand):
                if media.link_path is not None:
                    self._files.unlink(rom_id, media.link_path)
                return replace(media, link_path=None)
            link_path = media_link_path(media_root, system, folder, stem, media.extension)
            names = [media_link_path(media_root, system, folder, stem, extension) for extension in lookup_order(folder)]
            if media.link_path is not None and media.link_path not in names:
                self._files.unlink(rom_id, media.link_path)
            outcome = self._files.link(
                rom_id, folder, media.extension, on_demand=on_demand, link_path=link_path, names=names
            )
        except (OSError, ValueError) as e:
            self._logger.warning(f"[esde] the {folder} file of ROM {rom_id} was not linked: {e}")
            return replace(media, link_path=None)
        if outcome == HELD:
            self._log_debug(f"[esde] no {folder} link for ROM {rom_id}: a file that is not Tender's holds {stem!r}")
            return replace(media, link_path=None)
        return replace(media, link_path=link_path)

    def _drop_io(self, rom_id: int, folder: MediaFolder, media: FolderMedia) -> None:
        """Unlink *media* and forget its copy; a change that cannot be made is logged."""
        try:
            if media.link_path is not None:
                self._files.unlink(rom_id, media.link_path)
            self._files.forget(rom_id, folder, on_demand=folder in ON_SELECTION_FOLDERS)
        except OSError as e:
            self._logger.warning(f"[esde] the {folder} file of ROM {rom_id} was not removed: {e}")

    def _remove_io(self, games: Mapping[int, GameMedia]) -> None:
        """Unlink every one of *games*' media and forget their copies."""
        for rom_id, game in games.items():
            for folder, media in game.folders.items():
                self._drop_io(rom_id, folder, media)
            try:
                self._files.forget_game(rom_id)
            except OSError as e:
                self._logger.warning(f"[esde] the media copies of ROM {rom_id} were not removed: {e}")

    def _leave_io(self, record: Mapping[int, GameMedia], media_root: str | None) -> int:
        """Remove every link of Tender's — those *record* names and any other below *media_root* — and every copy.

        Answers how many links went.
        """
        removed = 0
        for rom_id, game in record.items():
            for folder, media in game.folders.items():
                if media.link_path is None:
                    continue
                try:
                    removed += int(self._files.unlink(rom_id, media.link_path))
                except OSError as e:
                    self._logger.warning(f"[esde] the {folder} link of ROM {rom_id} was not removed: {e}")
        if media_root is not None:
            removed += self._files.unlink_all(media_root)
        try:
            self._files.forget_all()
        except OSError as e:
            self._logger.warning(f"[esde] the media copies were not removed: {e}")
        return removed

    def _evict_io(self, doomed: Sequence[tuple[int, MediaFolder, FolderMedia]]) -> None:
        for rom_id, folder, media in doomed:
            self._drop_io(rom_id, folder, media)
