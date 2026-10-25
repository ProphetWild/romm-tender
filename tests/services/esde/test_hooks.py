"""``EsdeHookService`` over fakes: which game a hook names, and what a start, an end, a selection and a startup do.

Every seam the service asks records into one ``calls`` list, so a test reads
the order the steps ran in as well as which ran. The downloads fake starts a
real task, as the download service does, whose end a case decides: an install
written, nothing written, or cancelled.
"""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
from dataclasses import dataclass, field
from typing import Any

import pytest
from fakes.fake_esde_stand_in_guard import FakeEsdeStandInGuard
from fakes.fake_resolved_path import FakeResolvedPath
from fakes.fake_unit_of_work import FakeUnitOfWork, FakeUnitOfWorkFactory
from fakes.system_time import FakeClock

from domain.esde_hook_path import unescape_esde_path
from domain.refusal import DomainRefused
from domain.rom import Rom
from domain.rom_install import RomInstall
from lib.errors import NotInstalled, Refused, RommConnectionError, ServerUnreachable
from lib.partial_failure import PartialFailure
from services.esde import EsdeHookService, EsdeHookServiceConfig
from services.esde.hooks import NotATenderGame

_GBA = "/roms/gba"
_STAND_IN = f"{_GBA}/Game (USA).gba"
_FOLDER_STAND_IN = "/roms/psx/FF7.m3u"


@dataclass
class _Downloads:
    """``start_download`` and ``task_for_rom`` as the download service answers them, recording into ``calls``."""

    calls: list[Any]
    uow: FakeUnitOfWork
    # What the task does once started: "install" writes the install row, "fail" writes nothing, "cancel" is cancelled.
    ends: str = "install"
    install_path: str = _STAND_IN
    refusal: BaseException | None = None
    answer: Any = None
    release: asyncio.Event = field(default_factory=asyncio.Event)
    tasks: dict[int, asyncio.Task[None]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.release.set()

    async def start_download(self, rom_id: int) -> Any:
        self.calls.append(("start_download", rom_id))
        if self.refusal is not None:
            raise self.refusal
        if self.answer is not None:
            return self.answer
        self.tasks[rom_id] = asyncio.get_running_loop().create_task(self._run(rom_id))
        return {"success": True, "message": "Download started"}

    def task_for_rom(self, rom_id: int) -> asyncio.Task[None] | None:
        task = self.tasks.get(rom_id)
        return task if task is not None and not task.done() else None

    async def _run(self, rom_id: int) -> None:
        await self.release.wait()
        self.calls.append(("download ended", rom_id))
        if self.ends == "cancel":
            raise asyncio.CancelledError
        if self.ends == "install":
            _install(self.uow, rom_id, self.install_path)


@dataclass
class _Saves:
    """The save sync's four launch calls, recording into ``calls``; each answers or raises what a case sets."""

    calls: list[Any]
    configured: bool = False
    setup_info: dict[str, Any] = field(
        default_factory=lambda: {
            "server_slots": [],
            "default_slot": "default",
            "recommended_action": "show_wizard",
            "server_query_failed": False,
        }
    )
    pull: Any = field(default_factory=lambda: {"success": True, "message": "Downloaded 0 save(s)", "synced": 0})
    pull_wait: asyncio.Event | None = None
    setup_wait: asyncio.Event | None = None
    setup_refusal: BaseException | None = None

    def is_save_tracking_configured(self, rom_id: int) -> dict[str, Any]:
        self.calls.append(("is_save_tracking_configured", rom_id))
        return {"configured": self.configured, "active_slot": "default" if self.configured else None}

    async def get_save_setup_info(self, rom_id: int) -> dict[str, Any]:
        self.calls.append(("get_save_setup_info", rom_id))
        if self.setup_wait is not None:
            await self.setup_wait.wait()
        if self.setup_refusal is not None:
            raise self.setup_refusal
        return self.setup_info

    async def confirm_slot_choice(self, rom_id: int, chosen_slot: str | None) -> dict[str, Any]:
        self.calls.append(("confirm_slot_choice", rom_id, chosen_slot))
        return {"success": True}

    async def pre_launch_sync(self, rom_id: int) -> dict[str, Any]:
        self.calls.append(("pre_launch_sync", rom_id))
        if self.pull_wait is not None:
            await self.pull_wait.wait()
        if isinstance(self.pull, BaseException):
            raise self.pull
        return self.pull


@dataclass(frozen=True)
class _FinalizeSync:
    offline: bool
    success: bool
    uploaded: int
    downloaded: int
    failure_toast: str | None
    conflicts_toast: str | None


@dataclass(frozen=True)
class _Finalized:
    total_seconds: int | None
    sync: _FinalizeSync


@dataclass
class _Session:
    calls: list[Any]
    start_refusal: BaseException | None = None
    end_refusal: BaseException | None = None
    finalized: _Finalized = field(
        default_factory=lambda: _Finalized(
            total_seconds=60,
            sync=_FinalizeSync(
                offline=False, success=True, uploaded=1, downloaded=0, failure_toast=None, conflicts_toast=None
            ),
        )
    )

    async def record_session_start(self, rom_id: int) -> dict[str, Any]:
        self.calls.append(("record_session_start", rom_id))
        if self.start_refusal is not None:
            raise self.start_refusal
        return {"success": True}

    async def finalize(self, rom_id: int) -> _Finalized:
        self.calls.append(("finalize", rom_id))
        if self.end_refusal is not None:
            raise self.end_refusal
        return self.finalized


@dataclass
class _Media:
    calls: list[Any]

    def fetch_on_selection(self, rom_id: int) -> None:
        self.calls.append(("fetch_on_selection", rom_id))


@dataclass
class _Gamelists:
    calls: list[Any]
    wait: asyncio.Event | None = None
    cancelled: bool = False

    async def write_pending(self) -> None:
        self.calls.append(("write_pending",))
        if self.wait is not None:
            try:
                await self.wait.wait()
            except asyncio.CancelledError:
                self.cancelled = True
                raise


def _install(uow: FakeUnitOfWork, rom_id: int, path: str, rom_dir: str | None = None) -> None:
    with uow:
        uow.rom_installs.save(
            RomInstall.mark_installed(
                rom_id=rom_id,
                file_path=path,
                rom_dir=rom_dir,
                platform_slug="gba",
                system="gba",
                installed_at="2026-01-01T00:00:00+00:00",
            )
        )


@dataclass
class Harness:
    service: EsdeHookService
    calls: list[Any]
    emit: Any
    uow: FakeUnitOfWork
    stand_ins: FakeEsdeStandInGuard
    downloads: _Downloads
    saves: _Saves
    session: _Session
    media: _Media
    gamelists: _Gamelists
    resolve: FakeResolvedPath
    clock: FakeClock

    def rom(self, rom_id: int, name: str) -> None:
        with self.uow:
            self.uow.roms.save(
                Rom.synced(
                    rom_id=rom_id,
                    platform_slug="gba",
                    name=name,
                    fs_name=f"{name}.gba",
                    shortcut_app_id=None,
                    synced_at="2026-01-01T00:00:00+00:00",
                )
            )

    def install(self, rom_id: int, path: str, rom_dir: str | None = None) -> None:
        """Record an install at *path*, and the game it is of where none is recorded yet."""
        with self.uow:
            known = self.uow.roms.get(rom_id) is not None
        if not known:
            self.rom(rom_id, "Other")
        _install(self.uow, rom_id, path, rom_dir)

    def events(self, name: str | None = None) -> list[Any]:
        sent = [call.args for call in self.emit.await_args_list]
        return [payload for event, payload in sent if name is None or event == name]

    def steps(self) -> list[str]:
        return [call[0] for call in self.calls]


def _harness(
    emit, logger, *, save_wait: float = 15.0, startup_wait: float = 15.0, media_and_gamelists: bool = True
) -> Harness:
    calls: list[Any] = []
    uow = FakeUnitOfWork()
    stand_ins = FakeEsdeStandInGuard({_STAND_IN: 7, _FOLDER_STAND_IN: 8})
    stand_ins.unlink = lambda path: None
    downloads = _Downloads(calls, uow)
    saves = _Saves(calls)
    session = _Session(calls)
    media = _Media(calls)
    gamelists = _Gamelists(calls)
    resolve = FakeResolvedPath()
    clock = FakeClock()
    service = EsdeHookService(
        config=EsdeHookServiceConfig(
            downloads=downloads,
            saves=saves,
            session_start=session,
            session_end=session,
            stand_ins=stand_ins,
            resolve_path=resolve,
            uow_factory=FakeUnitOfWorkFactory(uow),
            emit=emit,
            clock=clock,
            loop=asyncio.get_running_loop(),
            logger=logger,
            log_debug=lambda msg: None,
            on_demand_media=media if media_and_gamelists else None,
            pending_gamelists=gamelists if media_and_gamelists else None,
            save_wait=save_wait,
            startup_wait=startup_wait,
        )
    )
    h = Harness(service, calls, emit, uow, stand_ins, downloads, saves, session, media, gamelists, resolve, clock)
    h.rom(7, "Game (USA)")
    h.rom(8, "Final Fantasy VII")
    return h


@pytest.fixture
async def h(emit, logger) -> Harness:
    return _harness(emit, logger)


async def _until(answered: Any, *, within: float = 5.0) -> None:
    """Wait for *answered* to answer something, for a step that runs off the loop."""
    deadline = asyncio.get_running_loop().time() + within
    while not answered():
        assert asyncio.get_running_loop().time() < deadline, "never answered"
        await asyncio.sleep(0.005)


def _escaped(path: str) -> str:
    """The path as a ``game-start`` script receives it for the characters these tests use."""
    escaped = path.replace(" ", "\\ ").replace("(", "\\(").replace(")", "\\)")
    assert unescape_esde_path(escaped) == path
    return escaped


class TestWhichGameAPathNames:
    async def test_a_stand_in_names_its_game(self, h):
        h.install(7, _STAND_IN)

        assert (await h.service.select(_STAND_IN))["rom_id"] == 7

    async def test_the_file_es_de_names_inside_a_folder_stand_in(self, h):
        assert (await h.service.select(f"{_FOLDER_STAND_IN}/FF7.m3u"))["rom_id"] == 8

    async def test_an_installs_recorded_file(self, h):
        h.install(9, "/roms/gba/Other.gba")

        assert (await h.service.select("/roms/gba/Other.gba"))["rom_id"] == 9

    async def test_a_file_in_an_installs_folder(self, h):
        h.install(9, "/roms/psx/Other/Other.m3u", rom_dir="/roms/psx/Other")

        assert (await h.service.select("/roms/psx/Other/Other (Disc 2).cue"))["rom_id"] == 9

    async def test_an_install_reached_through_a_link_in_its_path(self, h):
        """ES-DE names a game under its own ROM folder, which can be a link to where Tender recorded it."""
        h.resolve.links["/home/deck/retrodeck/roms"] = "/run/media/sd/roms"
        h.install(9, "/run/media/sd/roms/gba/Other.gba")

        assert (await h.service.select("/home/deck/retrodeck/roms/gba/Other.gba"))["rom_id"] == 9

    async def test_an_escaped_path_is_read_back_first(self, h):
        h.install(7, _STAND_IN)

        assert (await h.service.prepare(_escaped(_STAND_IN), True))["rom_id"] == 7

    @pytest.mark.parametrize("hook", ["prepare", "finish", "select"])
    async def test_any_other_path_is_refused_and_nothing_is_done(self, h, hook):
        args = ("/roms/gba/Not Tenders.gba", False) if hook != "select" else ("/roms/gba/Not Tenders.gba",)

        with pytest.raises(NotATenderGame) as refused:
            await getattr(h.service, hook)(*args)

        assert refused.value.reason == "not_a_tender_game"
        assert h.calls == []
        assert h.events() == []


class TestPreparingAStandIn:
    async def test_it_downloads_then_opens_the_session_confirms_the_slot_and_pulls_the_saves_in_that_order(self, h):
        answer = await h.service.prepare(_escaped(_STAND_IN), True)

        assert h.steps() == [
            "start_download",
            "download ended",
            "record_session_start",
            "is_save_tracking_configured",
            "get_save_setup_info",
            "confirm_slot_choice",
            "pre_launch_sync",
        ]
        assert answer["success"] is True
        assert answer["downloaded"] is True

    async def test_it_says_it_downloads_before_the_download_ends(self, h):
        h.downloads.release.clear()
        prepared = asyncio.ensure_future(h.service.prepare(_STAND_IN, False))
        await _until(lambda: h.events("esde_launch"))

        assert h.events("esde_launch") == [{"stage": "downloading", "rom_id": 7, "name": "Game (USA)"}]
        assert "download ended" not in h.steps()
        h.downloads.release.set()
        await prepared

    async def test_the_download_is_never_bounded(self, emit, logger):
        h = _harness(emit, logger, save_wait=0.01)
        h.downloads.release.clear()
        prepared = asyncio.ensure_future(h.service.prepare(_STAND_IN, False))
        await asyncio.sleep(0.1)

        assert not prepared.done()
        h.downloads.release.set()
        assert (await prepared)["downloaded"] is True

    @pytest.mark.parametrize("ends", ["fail", "cancel"])
    async def test_a_download_that_lands_nothing_is_said_and_runs_no_session_and_no_save_pull(self, h, ends):
        h.downloads.ends = ends

        with pytest.raises(Refused) as refused:
            await h.service.prepare(_STAND_IN, False)

        assert refused.value.reason == "download_failed"
        assert h.steps() == ["start_download", "download ended"]
        [said] = [event for event in h.events("esde_launch") if event["stage"] == "not_downloaded"]
        assert said["rom_id"] == 7
        assert said["name"] == "Game (USA)"
        assert said["message"]

    @pytest.mark.parametrize(
        "question",
        [
            DomainRefused("adoption_candidates", "This game is already on disk."),
            DomainRefused("target_occupied", "Something is there."),
            DomainRefused("unusable_namesake", "A namesake."),
        ],
    )
    async def test_a_question_the_download_asks_is_a_failure_that_says_to_open_tender(self, h, question):
        h.downloads.refusal = question

        with pytest.raises(DomainRefused) as refused:
            await h.service.prepare(_STAND_IN, False)

        assert refused.value is question
        assert h.steps() == ["start_download"]
        [said] = h.events("esde_launch")
        assert said["stage"] == "not_downloaded"
        assert "Open Tender" in said["message"]

    async def test_a_refused_download_says_why(self, h):
        h.downloads.refusal = Refused("insufficient_space", "Not enough disk space (1MB free, need 2MB)")

        with pytest.raises(Refused):
            await h.service.prepare(_STAND_IN, False)

        [said] = h.events("esde_launch")
        assert said == {
            "stage": "not_downloaded",
            "rom_id": 7,
            "name": "Game (USA)",
            "message": "Not enough disk space (1MB free, need 2MB)",
        }

    async def test_a_download_rom_m_could_not_answer_for_says_so(self, h):
        h.downloads.refusal = RommConnectionError("refused")

        with pytest.raises(RommConnectionError):
            await h.service.prepare(_STAND_IN, False)

        [said] = h.events("esde_launch")
        assert said["stage"] == "not_downloaded"
        assert "server" in said["message"].lower() or "romm" in said["message"].lower()

    async def test_a_partial_failure_from_the_start_lands_nothing_and_is_said(self, h):
        h.downloads.answer = PartialFailure(reason="replace_failed", message="Nothing was replaced.")

        with pytest.raises(Refused) as refused:
            await h.service.prepare(_STAND_IN, False)

        assert refused.value.reason == "download_failed"
        [said] = h.events("esde_launch")
        assert said["message"] == "Nothing was replaced."

    async def test_a_download_already_under_way_is_waited_for(self, h):
        h.downloads.release.clear()
        h.downloads.tasks[7] = asyncio.get_running_loop().create_task(h.downloads._run(7))
        h.downloads.refusal = Refused("already_downloading", "Already downloading")
        prepared = asyncio.ensure_future(h.service.prepare(_STAND_IN, False))
        await asyncio.sleep(0.01)
        h.downloads.release.set()

        answer = await prepared

        assert answer["downloaded"] is True
        assert "pre_launch_sync" in h.steps()

    async def test_a_download_already_under_way_that_ended_meanwhile_is_judged_by_its_install(self, h):
        h.downloads.refusal = Refused("already_downloading", "Already downloading")

        with pytest.raises(Refused) as refused:
            await h.service.prepare(_STAND_IN, False)

        assert refused.value.reason == "download_failed"
        assert [event["stage"] for event in h.events("esde_launch")] == ["not_downloaded"]

    async def test_a_download_that_lands_under_another_name_says_to_restart_es_de_and_pulls_nothing(self, h):
        h.downloads.install_path = f"{_GBA}/Game (USA) (Rev 1).gba"

        answer = await h.service.prepare(_STAND_IN, False)

        assert answer["listed"] is False
        assert "record_session_start" not in h.steps()
        assert "pre_launch_sync" not in h.steps()
        assert {"stage": "listed_elsewhere", "rom_id": 7, "name": "Game (USA)"} in h.events("esde_launch")


class TestPreparingAnInstalledGame:
    @pytest.fixture(autouse=True)
    def installed(self, h):
        h.install(9, "/roms/gba/Other.gba")

    async def test_it_skips_the_download(self, h):
        answer = await h.service.prepare("/roms/gba/Other.gba", False)

        assert "start_download" not in h.steps()
        assert answer["downloaded"] is False
        assert h.steps()[0] == "record_session_start"

    async def test_a_slot_already_confirmed_is_not_asked_about(self, h):
        h.saves.configured = True

        await h.service.prepare("/roms/gba/Other.gba", False)

        assert "get_save_setup_info" not in h.steps()
        assert "confirm_slot_choice" not in h.steps()

    async def test_a_slot_the_user_must_choose_is_not_confirmed_and_the_saves_are_still_pulled(self, h):
        h.saves.setup_info = {**h.saves.setup_info, "server_slots": [{"slot": "pc"}]}

        await h.service.prepare("/roms/gba/Other.gba", False)

        assert "confirm_slot_choice" not in h.steps()
        assert "pre_launch_sync" in h.steps()

    async def test_the_default_slot_is_confirmed_where_nothing_is_on_either_side(self, h):
        await h.service.prepare("/roms/gba/Other.gba", False)

        assert ("confirm_slot_choice", 9, "default") in h.calls

    async def test_a_session_that_could_not_be_opened_still_lets_the_launch_go_on(self, h):
        h.session.start_refusal = Refused("prune_active", "A cleanup is running")

        answer = await h.service.prepare("/roms/gba/Other.gba", False)

        assert answer["success"] is True
        assert "pre_launch_sync" in h.steps()

    async def test_saves_it_downloaded_are_said(self, h):
        h.saves.pull = {"success": True, "synced": 1, "uploaded": 0, "downloaded": 1, "conflicts": []}

        await h.service.prepare("/roms/gba/Other.gba", False)

        [said] = h.events("esde_launch")
        assert said == {
            "stage": "saves",
            "rom_id": 9,
            "name": "Other",
            "sync": {"success": True, "uploaded": 0, "downloaded": 1, "failure_toast": None, "conflicts_toast": None},
        }

    async def test_conflicts_are_said(self, h):
        h.saves.pull = {"success": True, "synced": 0, "conflicts": [{"filename": "a.srm"}, {"filename": "b.srm"}]}

        await h.service.prepare("/roms/gba/Other.gba", False)

        [said] = h.events("esde_launch")
        assert said["sync"]["conflicts_toast"].startswith("2 save conflicts need resolution")

    async def test_an_offline_pull_is_said_and_the_hook_still_answers(self, h):
        h.saves.pull = ServerUnreachable("Server offline", synced=0, offline=True)

        answer = await h.service.prepare("/roms/gba/Other.gba", False)

        assert answer["success"] is True
        [said] = h.events("esde_launch")
        assert said["sync"]["success"] is False
        assert "offline" in said["sync"]["failure_toast"].lower()

    async def test_a_refused_pull_says_why(self, h):
        h.saves.pull = Refused("blocked_by_update", "Tender is installing an update and will restart in a moment.")

        answer = await h.service.prepare("/roms/gba/Other.gba", False)

        assert answer["success"] is True
        [said] = h.events("esde_launch")
        assert "Tender is installing an update" in said["sync"]["failure_toast"]

    async def test_a_benign_skip_says_nothing_wrong(self, h):
        h.saves.pull = Refused("savefiles_in_content_dir", "Saves are written beside the game", synced=0)

        await h.service.prepare("/roms/gba/Other.gba", False)

        [said] = h.events("esde_launch")
        assert said["sync"]["success"] is True
        assert said["sync"]["failure_toast"] is None

    async def test_a_pull_rom_m_could_not_answer_says_so(self, h):
        h.saves.pull = RommConnectionError("reset")

        answer = await h.service.prepare("/roms/gba/Other.gba", False)

        assert answer["success"] is True
        [said] = h.events("esde_launch")
        assert said["sync"]["failure_toast"]

    async def test_a_pull_with_errors_is_said(self, h):
        h.saves.pull = {"success": False, "message": "Downloaded 0 save(s), 1 error(s)", "synced": 0, "errors": ["x"]}

        await h.service.prepare("/roms/gba/Other.gba", False)

        [said] = h.events("esde_launch")
        assert "1 error(s)" in said["sync"]["failure_toast"]

    async def test_a_failed_pull_that_says_nothing_is_still_said(self, h):
        h.saves.pull = {"success": False, "synced": 0}

        await h.service.prepare("/roms/gba/Other.gba", False)

        [said] = h.events("esde_launch")
        assert said["sync"]["failure_toast"] == "Some saves did not sync before the game started."


class TestTheSlotIsSettledOrLeft:
    async def test_a_setup_read_that_outlasts_the_wait_is_logged_and_the_saves_are_still_pulled(
        self, emit, logger, caplog
    ):
        h = _harness(emit, logger, save_wait=0.05)
        h.install(9, "/roms/gba/Other.gba")
        h.saves.setup_wait = asyncio.Event()

        with caplog.at_level("WARNING", logger=logger.name):
            answer = await asyncio.wait_for(h.service.prepare("/roms/gba/Other.gba", False), 5)

        assert answer["success"] is True
        assert "confirm_slot_choice" not in h.steps()
        assert "pre_launch_sync" in h.steps()
        assert "was not settled" in caplog.text
        h.saves.setup_wait.set()
        await h.service.shutdown()

    async def test_a_setup_read_that_raises_is_logged_and_the_saves_are_still_pulled(self, h, logger, caplog):
        h.install(9, "/roms/gba/Other.gba")
        h.saves.setup_refusal = RommConnectionError("refused")

        with caplog.at_level("WARNING", logger=logger.name):
            answer = await h.service.prepare("/roms/gba/Other.gba", False)

        assert answer["success"] is True
        assert "pre_launch_sync" in h.steps()
        assert "was not confirmed" in caplog.text


class TestTheSavePullIsBounded:
    async def test_a_pull_that_outlasts_the_wait_is_said_and_left_to_finish(self, emit, logger):
        h = _harness(emit, logger, save_wait=0.05)
        h.install(9, "/roms/gba/Other.gba")
        h.saves.pull_wait = asyncio.Event()

        answer = await asyncio.wait_for(h.service.prepare("/roms/gba/Other.gba", False), 5)

        assert answer["success"] is True
        [said] = h.events("esde_launch")
        assert "still syncing" in said["sync"]["failure_toast"].lower()
        h.saves.pull_wait.set()
        await h.service.shutdown()

    async def test_a_pull_left_running_that_fails_later_is_logged(self, emit, logger, caplog):
        h = _harness(emit, logger, save_wait=0.01)
        h.install(9, "/roms/gba/Other.gba")
        h.saves.pull_wait = asyncio.Event()
        h.saves.pull = RuntimeError("the pull broke late")
        await h.service.prepare("/roms/gba/Other.gba", False)

        with caplog.at_level("WARNING", logger=logger.name):
            h.saves.pull_wait.set()
            await _until(lambda: "the pull broke late" in caplog.text)

        assert "which a start stopped waiting for" in caplog.text

    async def test_a_pull_that_raises_anything_else_is_said_and_logged(self, h, logger, caplog):
        h.install(9, "/roms/gba/Other.gba")
        h.saves.pull = RuntimeError("a bug")

        with caplog.at_level("WARNING", logger=logger.name):
            answer = await h.service.prepare("/roms/gba/Other.gba", False)

        assert answer["success"] is True
        [said] = h.events("esde_launch")
        assert said["sync"]["failure_toast"] == "Saves were not synced before the game started."
        assert "a bug" in caplog.text

    async def test_the_shutdown_ends_a_pull_left_running(self, emit, logger):
        h = _harness(emit, logger, save_wait=0.01)
        h.install(9, "/roms/gba/Other.gba")
        h.saves.pull_wait = asyncio.Event()
        await h.service.prepare("/roms/gba/Other.gba", False)

        await asyncio.wait_for(h.service.shutdown(), 5)


class TestFinishing:
    async def test_an_installed_game_is_finalized_and_its_result_said(self, h):
        h.install(7, _STAND_IN)

        answer = await h.service.finish(_escaped(_STAND_IN), True)

        assert answer == {"success": True, "rom_id": 7}
        assert h.steps() == ["finalize"]
        [said] = h.events("esde_session")
        assert said == {"rom_id": 7, "name": "Game (USA)", "result": dataclasses.asdict(h.session.finalized)}

    async def test_a_refused_finalize_is_said_and_answered(self, h):
        h.install(7, _STAND_IN)
        h.session.end_refusal = Refused("prune_active", "A cleanup is running")

        with pytest.raises(Refused):
            await h.service.finish(_STAND_IN, False)

        [said] = h.events("esde_session")
        assert said == {
            "rom_id": 7,
            "name": "Game (USA)",
            "refusal": {"reason": "prune_active", "message": "A cleanup is running"},
        }

    async def test_a_stand_in_never_downloaded_has_no_session_to_end(self, h):
        with pytest.raises(NotInstalled):
            await h.service.finish(_STAND_IN, False)

        assert h.calls == []
        assert h.events() == []


class TestSelecting:
    async def test_the_game_is_handed_to_the_media_fetched_on_selection(self, h):
        answer = await h.service.select(_STAND_IN)

        assert answer == {"success": True, "rom_id": 7}
        assert h.calls == [("fetch_on_selection", 7)]

    async def test_a_selection_names_the_plain_path(self, h):
        """``game-select`` is passed the path unescaped, so a backslash in it is the path's own."""
        with pytest.raises(NotATenderGame):
            await h.service.select(_escaped(_STAND_IN))


class TestEsDesStart:
    async def test_the_held_back_gamelists_are_written(self, h):
        answer = await h.service.startup()

        assert answer == {"success": True, "finished": True}
        assert h.calls == [("write_pending",)]

    async def test_the_write_is_bounded_and_cancelled_past_its_bound(self, emit, logger):
        h = _harness(emit, logger, startup_wait=0.05)
        h.gamelists.wait = asyncio.Event()

        answer = await asyncio.wait_for(h.service.startup(), 5)

        assert answer == {"success": True, "finished": False}
        assert h.gamelists.cancelled is True


class TestWithNothingWiredForMediaOrGamelists:
    @pytest.fixture
    async def bare(self, emit, logger) -> Harness:
        return _harness(emit, logger, media_and_gamelists=False)

    async def test_a_selection_answers_and_fetches_nothing(self, bare):
        assert await bare.service.select(_STAND_IN) == {"success": True, "rom_id": 7}
        assert bare.calls == []

    async def test_es_des_start_answers_at_once(self, bare):
        assert await bare.service.startup() == {"success": True, "finished": True}
        assert bare.calls == []


class TestWhenEsDeLastCalled:
    async def test_nothing_before_a_first_call(self, h):
        assert h.service.last_call is None

    @pytest.mark.parametrize(
        ("hook", "args"),
        [("startup", ()), ("select", (_STAND_IN,)), ("finish", ("/roms/gba/Not Tenders.gba", False))],
    )
    async def test_every_call_is_kept_a_refused_one_included(self, h, hook, args):
        with contextlib.suppress(Refused, DomainRefused):
            await getattr(h.service, hook)(*args)

        call = h.service.last_call
        assert call is not None
        assert call.hook == hook
        assert call.at == h.clock.now().isoformat()
