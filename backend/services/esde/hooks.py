"""ES-DE's hooks: what one call from its event scripts does for a game of Tender's.

A path is Tender's when the stand-in guard names a game for it, which it does
for a stand-in and for the file ES-DE names inside a stand-in folder, or when
it names an install (``domain/esde_hook_path.py``), every path compared with
its symlinks resolved. Anything else is refused with ``not_a_tender_game`` and
nothing is done.

**A start is never held up by what cannot help it.** ``prepare`` downloads a
game that is not downloaded yet, waiting for as long as the download takes,
since ES-DE waits for it; then it opens the play session, confirms the save
slot where nobody has to be asked, and pulls the saves, the slot and the pull
each waited for no longer than the panel waits for them. Every failure is said
in an ``esde_launch`` event the panel toasts, and a game that is there to start
is answered as ready whatever its saves did. A download that landed nothing is
answered with its refusal, since the game is not there to start. ``finish``
ends the session and pushes the saves (``SessionFinalizer``) and says what that
did in an ``esde_session`` event.

**It checks no conflict rule of its own.** Every use case it calls checks its
own at its entry, and a refusal one raises is said and answered like any other.

When ES-DE last called is kept in memory, for the settings to show.
"""

from __future__ import annotations

import asyncio
import dataclasses
import os
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from domain.esde_hook_path import InstalledPaths, installed_game_at, unescape_esde_path
from domain.refusal import DomainRefused
from domain.save_answer import BENIGN_SYNC_SKIP_REASONS
from domain.save_slot_setup import slot_to_confirm
from lib.background_tasks import BackgroundTasks
from lib.errors import NamedRefused, NotInstalled, Refused, RommApiError, classify_error
from lib.partial_failure import PartialFailure

if TYPE_CHECKING:
    import logging
    from collections.abc import Coroutine

    from domain.rom_install import RomInstall
    from services.protocols import (
        Clock,
        DebugLogger,
        EsdeOnDemandMedia,
        EsdePendingGamelists,
        EsdeStandInGuard,
        EventEmitter,
        LaunchDownloader,
        LaunchSaveSync,
        ResolvedPathFn,
        SessionFinalizer,
        SessionStartRecorder,
        UnitOfWorkFactory,
    )

# How long a start waits for each of the slot confirmation and the save pull:
# the panel's own wait for the pre-launch sync (SERVER_CALL_LIMIT_MS,
# frontend/src/utils/launchGate.ts).
SAVE_WAIT_SECONDS = 15.0
# How long ES-DE's start, which it is held for, waits for the gamelists.
STARTUP_WAIT_SECONDS = 15.0

# The refusals a download's gate raises to ask the user something
# (``DownloadTargetGateFn``); a start from ES-DE has nobody to ask.
_QUESTIONS = frozenset({"target_occupied", "adoption_candidates", "unusable_namesake", "candidate_vanished"})

_ANSWER_IN_TENDER = "Tender has a question about it first. Open Tender and download it there."
_DOWNLOAD_FAILED = "The download did not finish, so ES-DE has nothing to start."
_DOWNLOAD_STOPPED = "The download was stopped, so ES-DE has nothing to start."
_PULL_OFFLINE = "RomM is offline, so the game starts with the saves on this device."
_PULL_STILL_RUNNING = "Saves are still syncing; the game starts without waiting for them."
_PULL_FAILED = "Saves were not synced before the game started"
_PULL_INCOMPLETE = "Some saves did not sync before the game started"


class NotATenderGame(NamedRefused):
    """The path an ES-DE script named is no stand-in of Tender's and no install."""

    reason = "not_a_tender_game"


@dataclass(frozen=True, slots=True)
class EsdeHookCall:
    """The last call ES-DE made: which hook, and when, in ISO 8601."""

    hook: str
    at: str


@dataclass(frozen=True, slots=True)
class SavePull:
    """What a start's save pull did, in the shape the panel toasts a session's sync in.

    ``success`` with the two counts makes the toast that names the direction;
    ``failure_toast`` says what went wrong, ``conflicts_toast`` what is waiting
    for the user.
    """

    success: bool
    uploaded: int
    downloaded: int
    failure_toast: str | None
    conflicts_toast: str | None


@dataclass(frozen=True, slots=True)
class _Game:
    rom_id: int
    name: str


@dataclass(frozen=True)
class EsdeHookServiceConfig:
    """Frozen wiring bundle handed to ``EsdeHookService.__init__``.

    ``downloads``, ``saves``, ``session_start`` and ``session_end`` are the use
    cases a start and an end run. ``stand_ins`` names the game a stand-in
    stands for; ``resolve_path`` spells a path with its symlinks resolved; and
    ``uow_factory`` reads the installs and the games' names.
    ``on_demand_media`` is handed a game ES-DE selected and
    ``pending_gamelists`` writes the gamelists ES-DE's start waits for;
    ``None`` for either does nothing. ``save_wait`` and ``startup_wait`` are the
    bounds a start puts on the save steps and ES-DE's start on the gamelists.
    """

    downloads: LaunchDownloader
    saves: LaunchSaveSync
    session_start: SessionStartRecorder
    session_end: SessionFinalizer
    stand_ins: EsdeStandInGuard
    resolve_path: ResolvedPathFn
    uow_factory: UnitOfWorkFactory
    emit: EventEmitter
    clock: Clock
    loop: asyncio.AbstractEventLoop
    logger: logging.Logger
    log_debug: DebugLogger
    on_demand_media: EsdeOnDemandMedia | None = None
    pending_gamelists: EsdePendingGamelists | None = None
    save_wait: float = SAVE_WAIT_SECONDS
    startup_wait: float = STARTUP_WAIT_SECONDS


class EsdeHookService:
    """Answers ES-DE's hooks for the games Tender lists there; implements ``EsdeHookHandler``."""

    def __init__(self, *, config: EsdeHookServiceConfig) -> None:
        self._downloads = config.downloads
        self._saves = config.saves
        self._session_start = config.session_start
        self._session_end = config.session_end
        self._stand_ins = config.stand_ins
        self._resolve_path = config.resolve_path
        self._uow_factory = config.uow_factory
        self._emit = config.emit
        self._clock = config.clock
        self._loop = config.loop
        self._logger = config.logger
        self._log_debug = config.log_debug
        self._media = config.on_demand_media
        self._gamelists = config.pending_gamelists
        self._save_wait = config.save_wait
        self._startup_wait = config.startup_wait
        self._tasks = BackgroundTasks(logger=config.logger)
        self._last_call: EsdeHookCall | None = None

    @property
    def last_call(self) -> EsdeHookCall | None:
        """The last call ES-DE made since this backend started, refused ones included; ``None`` before the first."""
        return self._last_call

    async def shutdown(self) -> None:
        """Cancel the save steps a start stopped waiting for, and wait until they have ended."""
        await self._tasks.shutdown()

    # ── The hooks ───────────────────────────────────────────────────

    async def startup(self) -> dict[str, Any]:
        """Write the gamelists held back while ES-DE ran, before it reads them; ``finished`` says whether in time.

        The write is cancelled once ES-DE has waited ``startup_wait`` for it:
        ES-DE reads its gamelists as soon as this answers, and one written after
        that would be written while it runs.
        """
        self._note("startup")
        if self._gamelists is None:
            return {"success": True, "finished": True}
        try:
            async with asyncio.timeout(self._startup_wait):
                await self._gamelists.write_pending()
        except TimeoutError:
            self._logger.warning(f"[esde] the gamelists were not written within the {self._startup_wait}s ES-DE waits")
            return {"success": True, "finished": False}
        return {"success": True, "finished": True}

    async def prepare(self, path: str, escaped: bool) -> dict[str, Any]:
        """Make the game at *path* ready to start: downloaded, its session open, its saves pulled.

        *escaped* says *path* is as a ``game-start`` script receives it. Answers
        ``{"success": True, "rom_id", "downloaded", "listed", "saves"}``:
        ``listed`` is ``False`` where the download landed under another name
        than ES-DE lists, and then nothing else is done and ``saves`` is
        ``None``; otherwise ``saves`` is what the pull did (:class:`SavePull`).
        Raises ``not_a_tender_game`` for a path that is no game of Tender's, and
        the download's refusal where it landed nothing.
        """
        self._note("prepare")
        named = unescape_esde_path(path) if escaped else path
        game = await self._game_at(named)
        downloaded = False
        if await self._loop.run_in_executor(None, self._install_io, game.rom_id) is None:
            install = await self._download(game)
            downloaded = True
            if not await self._loop.run_in_executor(None, self._names_install_io, named, install):
                await self._emit("esde_launch", {"stage": "listed_elsewhere", "rom_id": game.rom_id, "name": game.name})
                return {"success": True, "rom_id": game.rom_id, "downloaded": True, "listed": False, "saves": None}
        await self._open_session(game)
        await self._confirm_slot(game)
        pull = await self._pull_saves(game)
        return {
            "success": True,
            "rom_id": game.rom_id,
            "downloaded": downloaded,
            "listed": True,
            "saves": dataclasses.asdict(pull),
        }

    async def finish(self, path: str, escaped: bool) -> dict[str, Any]:
        """End the play session of the game at *path* and push its saves, as a game's stop in Steam does.

        Answers ``{"success": True, "rom_id"}`` and says the result in an
        ``esde_session`` event; a refusal of the session's end is said there
        too, and raised. Raises ``not_installed`` for a game never downloaded,
        which has no session to end.
        """
        self._note("finish")
        named = unescape_esde_path(path) if escaped else path
        game = await self._game_at(named)
        if await self._loop.run_in_executor(None, self._install_io, game.rom_id) is None:
            raise NotInstalled(f"{game.name} is not downloaded, so it has no session to end")
        try:
            result = await self._session_end.finalize(game.rom_id)
        except (Refused, DomainRefused) as refusal:
            await self._emit(
                "esde_session",
                {
                    "rom_id": game.rom_id,
                    "name": game.name,
                    "refusal": {"reason": refusal.reason, "message": refusal.message},
                },
            )
            raise
        await self._emit(
            "esde_session", {"rom_id": game.rom_id, "name": game.name, "result": dataclasses.asdict(result)}
        )
        return {"success": True, "rom_id": game.rom_id}

    async def select(self, path: str) -> dict[str, Any]:
        """Hand the game ES-DE selected, at the plain *path*, to the media fetched on selection; answers at once."""
        self._note("select")
        game = await self._game_at(path)
        if self._media is not None:
            self._media.fetch_on_selection(game.rom_id)
        return {"success": True, "rom_id": game.rom_id}

    # ── A start's steps ─────────────────────────────────────────────

    async def _download(self, game: _Game) -> RomInstall:
        """Download *game* and wait for it to land, however long that takes; answer its install.

        A download of the game already under way is waited for. A download that
        lands nothing is said and raised: the start's own refusal, or
        ``download_failed`` for one that started and failed or was stopped.
        """
        try:
            started = await self._downloads.start_download(game.rom_id)
        except (Refused, DomainRefused) as refusal:
            if refusal.reason != "already_downloading":
                message = _ANSWER_IN_TENDER if refusal.reason in _QUESTIONS else refusal.message
                await self._say_not_downloaded(game, message)
                raise
            started = None
        except RommApiError as e:
            await self._say_not_downloaded(game, classify_error(e)[1])
            raise
        if isinstance(started, PartialFailure):
            await self._say_not_downloaded(game, started.message)
            raise Refused("download_failed", started.message)
        task = self._downloads.task_for_rom(game.rom_id)
        if task is not None:
            await self._emit("esde_launch", {"stage": "downloading", "rom_id": game.rom_id, "name": game.name})
            # Waited for, never awaited: a cancelled hook must not cancel the download.
            await asyncio.wait({task})
        install = await self._loop.run_in_executor(None, self._install_io, game.rom_id)
        if install is not None:
            return install
        message = _DOWNLOAD_STOPPED if task is not None and task.cancelled() else _DOWNLOAD_FAILED
        await self._say_not_downloaded(game, message)
        raise Refused("download_failed", message)

    async def _say_not_downloaded(self, game: _Game, message: str) -> None:
        await self._emit(
            "esde_launch", {"stage": "not_downloaded", "rom_id": game.rom_id, "name": game.name, "message": message}
        )

    async def _open_session(self, game: _Game) -> None:
        """Open *game*'s play session; one that could not be opened is logged, and the start goes on."""
        try:
            await self._session_start.record_session_start(game.rom_id)
        except Exception as e:
            self._logger.warning(f"[esde] the session of ROM {game.rom_id} was not opened: {e!r}")

    async def _confirm_slot(self, game: _Game) -> None:
        """Confirm *game*'s save slot where nobody has to be asked (``slot_to_confirm``), as the panel's start does.

        Silent, as the panel's is: what could not be settled is logged, and the
        start goes on unconfirmed.
        """
        task = await self._within_save_wait(self._confirm_slot_steps(game), "the save slot's confirmation")
        if task is None:
            self._logger.warning(f"[esde] the save slot of ROM {game.rom_id} was not settled within {self._save_wait}s")
            return
        try:
            task.result()
        except Exception as e:
            self._logger.warning(f"[esde] the save slot of ROM {game.rom_id} was not confirmed: {e!r}")

    async def _confirm_slot_steps(self, game: _Game) -> None:
        tracking = await self._loop.run_in_executor(None, self._saves.is_save_tracking_configured, game.rom_id)
        if tracking.get("configured"):
            return
        slot = slot_to_confirm(await self._saves.get_save_setup_info(game.rom_id))
        if slot is None:
            self._log_debug(f"[esde] ROM {game.rom_id}'s save slot is the user's to choose; it starts unconfirmed")
            return
        await self._saves.confirm_slot_choice(game.rom_id, slot)

    async def _pull_saves(self, game: _Game) -> SavePull:
        """Pull *game*'s saves before it starts and say what that did; never raises.

        A pull that outlasts the wait is said and left to finish on its own.
        """
        task = await self._within_save_wait(self._saves.pre_launch_sync(game.rom_id), "the pre-launch save pull")
        pull = _failed_pull(_PULL_STILL_RUNNING) if task is None else self._read_pull(game, task)
        await self._emit(
            "esde_launch",
            {"stage": "saves", "rom_id": game.rom_id, "name": game.name, "sync": dataclasses.asdict(pull)},
        )
        return pull

    def _read_pull(self, game: _Game, task: asyncio.Task[dict[str, Any]]) -> SavePull:
        try:
            result = task.result()
        except (Refused, DomainRefused) as refusal:
            if refusal.reason in BENIGN_SYNC_SKIP_REASONS:
                return SavePull(success=True, uploaded=0, downloaded=0, failure_toast=None, conflicts_toast=None)
            offline = refusal.details.get("offline") is True
            return _failed_pull(_PULL_OFFLINE if offline else f"{_PULL_FAILED}: {refusal.message}")
        except RommApiError as e:
            return _failed_pull(f"{_PULL_FAILED}: {classify_error(e)[1]}")
        except Exception as e:
            self._logger.warning(f"[esde] the pre-launch save pull of ROM {game.rom_id} failed: {e!r}")
            return _failed_pull(f"{_PULL_FAILED}.")
        success = bool(result.get("success"))
        message = result.get("message")
        conflicts = result.get("conflicts")
        failure = None
        if not success:
            failure = (
                f"{_PULL_INCOMPLETE}: {message}" if isinstance(message, str) and message else f"{_PULL_INCOMPLETE}."
            )
        return SavePull(
            success=success,
            uploaded=_count(result.get("uploaded")),
            downloaded=_count(result.get("downloaded")),
            failure_toast=failure,
            conflicts_toast=_conflicts_toast(len(conflicts) if isinstance(conflicts, list) else 0),
        )

    async def _within_save_wait[T](self, work: Coroutine[Any, Any, T], name: str) -> asyncio.Task[T] | None:
        """Run *work* for up to ``save_wait``: its task once it ended, ``None`` while it runs on.

        Work that outlasts the wait is not cancelled, since stopping a save
        sync partway could leave a save and its record apart; it is held until
        it ends, or until :meth:`shutdown`, and what it raises then is logged.
        """
        task = self._tasks.start_awaited(work, name)
        done, _running = await asyncio.wait({task}, timeout=self._save_wait)
        if task in done:
            return task
        task.add_done_callback(self._log_late_failure)
        return None

    def _log_late_failure(self, task: asyncio.Task[Any]) -> None:
        if not task.cancelled() and task.exception() is not None:
            self._logger.warning(
                f"[esde] {task.get_name()}, which a start stopped waiting for, failed: {task.exception()!r}"
            )

    # ── Blocking work, run off the loop ─────────────────────────────

    async def _game_at(self, path: str) -> _Game:
        game = await self._loop.run_in_executor(None, self._game_at_io, path)
        if game is None:
            raise NotATenderGame("Tender lists no game at this path in ES-DE", path=path)
        return game

    def _game_at_io(self, path: str) -> _Game | None:
        """The game a stand-in at *path* stands for, else the one whose install *path* names; ``None`` for neither."""
        rom_id = self._stand_ins.do_stand_in_at(path)
        if rom_id is None:
            with self._uow_factory() as uow:
                installs = list(uow.rom_installs.iter_all())
            rom_id = installed_game_at(
                self._resolve_path(path),
                self._resolve_path(os.path.dirname(path)),
                [self._resolved(install) for install in installs],
            )
        if rom_id is None:
            return None
        with self._uow_factory() as uow:
            rom = uow.roms.get(rom_id)
        return _Game(rom_id=rom_id, name=rom.name if rom is not None and rom.name else os.path.basename(path))

    def _install_io(self, rom_id: int) -> RomInstall | None:
        with self._uow_factory() as uow:
            return uow.rom_installs.get(rom_id)

    def _names_install_io(self, path: str, install: RomInstall) -> bool:
        """Whether ES-DE's *path* names *install*, which a download just wrote."""
        resolved = self._resolved(install)
        named = installed_game_at(self._resolve_path(path), self._resolve_path(os.path.dirname(path)), [resolved])
        return named == install.rom_id

    def _resolved(self, install: RomInstall) -> InstalledPaths:
        return InstalledPaths(
            rom_id=install.rom_id,
            file_path=self._resolve_path(install.file_path),
            rom_dir=None if install.rom_dir is None else self._resolve_path(install.rom_dir),
        )

    def _note(self, hook: str) -> None:
        self._last_call = EsdeHookCall(hook=hook, at=self._clock.now().isoformat())


def _count(value: object) -> int:
    return value if isinstance(value, int) else 0


def _failed_pull(toast: str) -> SavePull:
    return SavePull(success=False, uploaded=0, downloaded=0, failure_toast=toast, conflicts_toast=None)


def _conflicts_toast(count: int) -> str | None:
    if count == 0:
        return None
    plural = "" if count == 1 else "s"
    return f"{count} save conflict{plural} need resolution — open Tender to resolve them"
