"""Bringing the backend up in the one order that makes the port file mean something.

The order is the invariant, so the observations are taken from inside the
sequence: each step records what the world looked like when it ran, and the
assertions are about those snapshots rather than about the end state, which
every order would produce alike.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import signal
import struct
from typing import TYPE_CHECKING

import pytest

from host.dispatch import CallDispatcher
from host.events import EventSink
from host.hooks import HOOK_HEADER_FILENAME, HOOK_PORT_FILENAME
from host.inject import InjectionSetup
from host.inject.bootstrap import MARKER, marker_present_expression
from host.inject.bundles import COEXISTENCE_PANEL, GLOBALS_BUNDLE, STANDALONE_PANEL, choose_bundles
from host.inject.recovery import ListedApp
from host.protocol import CLOSE_STRANDED_PANEL_RELOADS
from host.runtime import AlreadyRunningError, BackendBuild, _where_the_running_one_is, run_backend
from host.single_instance import PortFile, SingleInstanceLock
from host.status import HostStatus
from lib.websocket_frames import OPCODE_CLOSE
from tests.host.conftest import FakeEndpoints, FakeHooks, close_at_once, close_listener, free_port
from tests.host.inject.fake_debugger import FakeDebugger, FakePage, FakeTarget
from tests.host.ws_client import WsTestClient, http_get

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Mapping

    from host.hooks import HookFn

LOGGER = logging.getLogger("test_runtime")


HOOK_TOKEN = "the-hook-token"


class Recorder:
    """Records what each start-up step saw, in the order the steps ran."""

    def __init__(self, port_file: PortFile, lock_path: str, hook_files_dir: str) -> None:
        self.port_file = port_file
        self.lock_path = lock_path
        self.hook_files_dir = hook_files_dir
        self.steps: list[str] = []
        self.port_once_running: int | None = None
        self.lock_held_at_build: bool | None = None
        self.hook_files_at_build: dict[str, str] | None = None
        self.esde_hooks: Mapping[str, HookFn] | None = None
        self.observe: Callable[[int], Awaitable[None]] | None = None
        self.shutdown_ran = asyncio.Event()

    async def build(self) -> BackendBuild:
        self.steps.append("build")
        contender = SingleInstanceLock(self.lock_path, retry_seconds=0.0)
        self.lock_held_at_build = not contender.acquire()
        contender.release()
        self.hook_files_at_build = read_hook_files(self.hook_files_dir)
        return BackendBuild(
            dispatcher=CallDispatcher(FakeEndpoints(), LOGGER),
            server_identity="romm-tender/0.0.0-test",
            shutdown=self.shutdown,
            esde_hooks=self.esde_hooks,
        )

    async def while_running(self) -> None:
        """Beside the run: once the port file names a port, note it, observe, and ask the run to end."""
        async with asyncio.timeout(10):
            while (port := self.port_file.read()) is None:
                await asyncio.sleep(0.01)
        self.port_once_running = port
        if self.observe is not None:
            await self.observe(port)
        os.kill(os.getpid(), signal.SIGTERM)

    async def shutdown(self) -> None:
        self.steps.append("shutdown")
        self.shutdown_ran.set()


@pytest.fixture
def default_sigterm():
    """Only run the signal cases where nothing else owns SIGTERM."""
    if signal.getsignal(signal.SIGTERM) is not signal.SIG_DFL:
        pytest.skip("something else already handles SIGTERM in this process")
    yield
    signal.signal(signal.SIGTERM, signal.SIG_DFL)


async def _run(tmp_path, recorder: Recorder, status: HostStatus, port: int, injection=None) -> None:
    """Drive ``run_backend`` on *port* so no test ever binds the real default."""
    static_root = tmp_path / "dist"
    static_root.mkdir(exist_ok=True)
    watcher = asyncio.ensure_future(recorder.while_running())
    try:
        await run_backend(
            injection=injection,
            build=recorder.build,
            events=EventSink(LOGGER),
            status=status,
            static_root=str(static_root),
            lock_path=recorder.lock_path,
            port_file_path=recorder.port_file.path,
            logger=LOGGER,
            token="the-admission-token",
            hook_token=HOOK_TOKEN,
            hook_files_dir=recorder.hook_files_dir,
            preferred_port=port,
        )
    finally:
        watcher.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await watcher


def read_hook_files(directory: str) -> dict[str, str]:
    """Every file in the hook files' directory by name, with what it holds; empty where there is none."""
    if not os.path.isdir(directory):
        return {}
    found: dict[str, str] = {}
    for name in sorted(os.listdir(directory)):
        with open(os.path.join(directory, name), encoding="utf-8") as handle:
            found[name] = handle.read()
    return found


@pytest.fixture
def recorder(tmp_path):
    return Recorder(
        PortFile(str(tmp_path / "run" / "port")),
        str(tmp_path / "data" / "backend.lock"),
        str(tmp_path / "data" / "esde-hooks"),
    )


class TestTheStartUpOrder:
    async def test_every_step_runs_once_and_in_order(self, tmp_path, recorder, default_sigterm):
        await _run(tmp_path, recorder, HostStatus(), free_port())

        assert recorder.steps == ["build", "shutdown"]

    async def test_the_lock_is_held_before_anything_is_built(self, tmp_path, recorder, default_sigterm):
        """Two backends would both migrate the schema and both run the repairs."""
        await _run(tmp_path, recorder, HostStatus(), free_port())

        assert recorder.lock_held_at_build is True

    async def test_the_port_file_names_the_bound_port_while_it_runs(self, tmp_path, recorder, default_sigterm):
        """'The port file is there' means 'the backend is ready' — nothing else has to."""
        status = HostStatus()

        await _run(tmp_path, recorder, status, free_port())

        assert recorder.port_once_running == status.port

    async def test_the_server_answers_while_it_runs(self, tmp_path, recorder, default_sigterm):
        status = HostStatus()
        task = asyncio.ensure_future(_run(tmp_path, recorder, status, free_port()))
        await asyncio.wait_for(recorder.shutdown_ran.wait(), 10)
        await task

        assert status.port != 0


class TestTheHookFiles:
    """What ES-DE's scripts read to find this backend: written once the port is bound, taken away with it."""

    async def test_while_it_runs_they_name_the_bound_port_and_this_runs_hook_token(
        self, tmp_path, recorder, default_sigterm
    ):
        seen: list[dict[str, str]] = []

        async def observe(_port: int) -> None:
            seen.append(read_hook_files(recorder.hook_files_dir))

        recorder.observe = observe
        status = HostStatus()

        await _run(tmp_path, recorder, status, free_port())

        assert seen == [
            {HOOK_HEADER_FILENAME: f"X-Tender-Hook-Token: {HOOK_TOKEN}\n", HOOK_PORT_FILENAME: str(status.port)}
        ]

    async def test_none_is_there_while_the_backend_is_built(self, tmp_path, recorder, default_sigterm):
        """Their being there means the port answers, as the port file's does."""
        await _run(tmp_path, recorder, HostStatus(), free_port())

        assert recorder.hook_files_at_build == {}

    async def test_what_an_earlier_run_left_behind_is_replaced(self, tmp_path, recorder, default_sigterm):
        """A crash leaves them; the next start writes over them before anything reads its port."""
        os.makedirs(recorder.hook_files_dir)
        stale = {HOOK_HEADER_FILENAME: "X-Tender-Hook-Token: an-earlier-runs-token\n", HOOK_PORT_FILENAME: "1"}
        for name, content in stale.items():
            with open(os.path.join(recorder.hook_files_dir, name), "w", encoding="utf-8") as handle:
                handle.write(content)
        seen: list[dict[str, str]] = []

        async def observe(_port: int) -> None:
            seen.append(read_hook_files(recorder.hook_files_dir))

        recorder.observe = observe
        status = HostStatus()

        await _run(tmp_path, recorder, status, free_port())

        assert recorder.hook_files_at_build == stale
        assert seen == [
            {HOOK_HEADER_FILENAME: f"X-Tender-Hook-Token: {HOOK_TOKEN}\n", HOOK_PORT_FILENAME: str(status.port)}
        ]

    async def test_they_are_taken_away_at_shutdown(self, tmp_path, recorder, default_sigterm):
        await _run(tmp_path, recorder, HostStatus(), free_port())

        assert read_hook_files(recorder.hook_files_dir) == {}

    async def test_a_hook_called_as_the_files_say_reaches_the_builds_routes(self, tmp_path, recorder, default_sigterm):
        hooks = FakeHooks()
        recorder.esde_hooks = hooks.routes()
        answered: list[int] = []

        async def observe(_port: int) -> None:
            files = read_hook_files(recorder.hook_files_dir)
            name, _, value = files[HOOK_HEADER_FILENAME].rstrip("\n").partition(": ")
            status, _, _ = await http_get(
                int(files[HOOK_PORT_FILENAME]), "/hooks/esde/select?path=%2Froms%2Fa.sfc", headers=[(name, value)]
            )
            answered.append(status)

        recorder.observe = observe

        await _run(tmp_path, recorder, HostStatus(), free_port())

        assert answered == [200]
        assert hooks.calls == [("select", {"path": "/roms/a.sfc"})]

    async def test_with_no_routes_built_a_hook_answers_503(self, tmp_path, recorder, default_sigterm):
        answered: list[int] = []

        async def observe(port: int) -> None:
            status, _, _ = await http_get(port, "/hooks/esde/startup", headers=[("X-Tender-Hook-Token", HOOK_TOKEN)])
            answered.append(status)

        recorder.observe = observe

        await _run(tmp_path, recorder, HostStatus(), free_port())

        assert answered == [503]

    async def test_a_place_they_cannot_be_written_costs_the_hooks_and_nothing_else(
        self, tmp_path, recorder, default_sigterm, caplog
    ):
        (tmp_path / "a-file").write_text("", encoding="utf-8")
        recorder.hook_files_dir = str(tmp_path / "a-file" / "esde-hooks")
        status = HostStatus()

        with caplog.at_level(logging.ERROR, logger=LOGGER.name):
            await _run(tmp_path, recorder, status, free_port())

        assert recorder.steps == ["build", "shutdown"]
        assert recorder.port_once_running == status.port
        assert any("ES-DE" in record.getMessage() for record in caplog.records if record.levelno >= logging.ERROR)


class TestShutdown:
    async def test_a_termination_signal_ends_the_run(self, tmp_path, recorder, default_sigterm):
        await asyncio.wait_for(_run(tmp_path, recorder, HostStatus(), free_port()), 10)

        assert recorder.shutdown_ran.is_set()

    async def test_the_port_file_is_taken_away(self, tmp_path, recorder, default_sigterm):
        await _run(tmp_path, recorder, HostStatus(), free_port())

        assert recorder.port_file.read() is None

    async def test_the_lock_is_let_go(self, tmp_path, recorder, default_sigterm):
        await _run(tmp_path, recorder, HostStatus(), free_port())

        successor = SingleInstanceLock(recorder.lock_path, retry_seconds=0.0)
        try:
            assert successor.acquire() is True
        finally:
            successor.release()

    async def test_the_port_stops_answering(self, tmp_path, recorder, default_sigterm):
        status = HostStatus()
        await _run(tmp_path, recorder, status, free_port())

        with pytest.raises(OSError):
            await http_get(status.port, "/index.js", token="anything")

    async def test_a_failing_shutdown_still_releases_the_lock(self, tmp_path, recorder, default_sigterm):
        async def broken_shutdown() -> None:
            recorder.shutdown_ran.set()
            raise RuntimeError("unload broke")

        recorder.shutdown = broken_shutdown  # type: ignore[method-assign]

        await _run(tmp_path, recorder, HostStatus(), free_port())

        successor = SingleInstanceLock(recorder.lock_path, retry_seconds=0.0)
        try:
            assert successor.acquire() is True
        finally:
            successor.release()


class TestABuildThatFails:
    """Nothing was built, so there is nothing to shut down."""

    @staticmethod
    def _break(recorder: Recorder) -> None:
        async def broken_build() -> BackendBuild:
            recorder.steps.append("build")
            raise RuntimeError("the schema migration broke")

        recorder.build = broken_build  # type: ignore[method-assign]

    async def test_the_error_surfaces(self, tmp_path, recorder):
        self._break(recorder)
        status, port = HostStatus(), free_port()

        with pytest.raises(RuntimeError, match="the schema migration broke"):
            await _run(tmp_path, recorder, status, port)

    async def test_nothing_is_shut_down(self, tmp_path, recorder):
        self._break(recorder)
        status, port = HostStatus(), free_port()

        with pytest.raises(RuntimeError):
            await _run(tmp_path, recorder, status, port)

        assert recorder.steps == ["build"]

    async def test_the_lock_is_let_go(self, tmp_path, recorder):
        self._break(recorder)
        status, port = HostStatus(), free_port()

        with pytest.raises(RuntimeError):
            await _run(tmp_path, recorder, status, port)

        successor = SingleInstanceLock(recorder.lock_path, retry_seconds=0.0)
        try:
            assert successor.acquire() is True
        finally:
            successor.release()


class TestASecondBackend:
    async def test_it_refuses_to_start(self, tmp_path, recorder):
        status, port = HostStatus(), free_port()
        holder = SingleInstanceLock(recorder.lock_path)
        holder.acquire()
        try:
            with pytest.raises(AlreadyRunningError):
                await _run(tmp_path, recorder, status, port)
        finally:
            holder.release()

        assert recorder.steps == []


class TestWhatTheRefusalTells:
    """The note and the proof, as the refusing process words them.

    Unit-level rather than through ``run_backend``, because each pass through
    that would first sit out the lock's whole retry window to establish the one
    fact these cases already assume.
    """

    async def test_a_port_that_answers_is_named_as_the_running_backend(self, tmp_path):
        listener = await asyncio.start_server(close_at_once, host="127.0.0.1", port=0)
        port = listener.sockets[0].getsockname()[1]
        note = PortFile(str(tmp_path / "port"))
        note.write(port)
        try:
            assert f"already running on 127.0.0.1:{port}" in _where_the_running_one_is(note)
        finally:
            await close_listener(listener)

    def test_a_port_nobody_answers_on_is_reported_as_a_leftover(self, tmp_path):
        """The file is a hint; connecting is the proof."""
        note = PortFile(str(tmp_path / "port"))
        note.write(free_port())

        assert "nothing answers there" in _where_the_running_one_is(note)

    def test_no_note_at_all_is_said_plainly(self, tmp_path):
        assert "left no port to find it by" in _where_the_running_one_is(PortFile(str(tmp_path / "port")))


class TestFailedStartUpSteps:
    def test_the_status_counts_them(self):
        status = HostStatus()

        status.record_failed_step("prune_orphaned_cover_cache")
        status.record_failed_step("cleanup_leftover_tmp_files")

        assert status.failed_startup_steps == ["prune_orphaned_cover_cache", "cleanup_leftover_tmp_files"]

    def test_a_clean_start_records_none(self):
        assert HostStatus().failed_startup_steps == []


class TestLoadingThePanelIntoSteam:
    """The composition seam: the run loads the panel, and stops loading it when it ends."""

    async def test_the_panel_is_loaded_while_the_backend_runs_and_the_socket_goes_with_it(
        self, tmp_path, recorder, default_sigterm
    ):
        page = FakePage(
            marker_expression=marker_present_expression(MARKER),
            ready_expression=choose_bundles(decky_is_serving=False).ready_when,
        )
        debugger = FakeDebugger(page)
        await debugger.start()
        debugger.targets = [FakeTarget(id="renderer", title="SharedJSContext")]
        debugger.handlers["Page.enable"] = lambda _params: {}

        static_root = tmp_path / "dist"
        static_root.mkdir(exist_ok=True)
        for name in (GLOBALS_BUNDLE, STANDALONE_PANEL, COEXISTENCE_PANEL):
            (static_root / name).write_text(f"// {name}\n", encoding="utf-8")

        async def while_running() -> None:
            async with asyncio.timeout(10):
                while not page.bootstraps:
                    await asyncio.sleep(0.01)
            os.kill(os.getpid(), signal.SIGTERM)

        recorder.while_running = while_running  # type: ignore[method-assign]
        try:
            await asyncio.wait_for(
                _run(
                    tmp_path,
                    recorder,
                    HostStatus(),
                    free_port(),
                    injection=InjectionSetup(
                        static_root=str(static_root),
                        state_dir=str(tmp_path / "state"),
                        user_home=str(tmp_path / "home"),
                        version="0.0.0-test",
                        debugger_port=debugger.port,
                        decky_port=free_port(),
                    ),
                ),
                20,
            )
            assert len(page.bootstraps) == 1
            assert debugger.connections == 0
        finally:
            await debugger.stop()

    async def test_the_steam_readings_answer_through_the_injector_once_it_exists(
        self, tmp_path, recorder, default_sigterm
    ):
        page = FakePage(
            marker_expression=marker_present_expression(MARKER),
            ready_expression=choose_bundles(decky_is_serving=False).ready_when,
            running_apps=["Celeste"],
        )
        debugger = FakeDebugger(page)
        await debugger.start()
        debugger.targets = [FakeTarget(id="renderer", title="SharedJSContext")]
        debugger.handlers["Page.enable"] = lambda _params: {}
        static_root = tmp_path / "dist"
        static_root.mkdir(exist_ok=True)
        for name in (GLOBALS_BUNDLE, STANDALONE_PANEL, COEXISTENCE_PANEL):
            (static_root / name).write_text(f"// {name}\n", encoding="utf-8")
        status = HostStatus()
        read_during_the_build: list[tuple[ListedApp, ...] | None] = []
        read_while_running: list[tuple[ListedApp, ...] | None] = []
        build = recorder.build

        async def reading_build():
            read_during_the_build.append(await status.steam.running_apps())
            return await build()

        async def while_running() -> None:
            async with asyncio.timeout(10):
                while not page.bootstraps:
                    await asyncio.sleep(0.01)
            read_while_running.append(await status.steam.running_apps())
            os.kill(os.getpid(), signal.SIGTERM)

        recorder.build = reading_build  # type: ignore[method-assign]
        recorder.while_running = while_running  # type: ignore[method-assign]
        try:
            await asyncio.wait_for(
                _run(
                    tmp_path,
                    recorder,
                    status,
                    free_port(),
                    injection=InjectionSetup(
                        static_root=str(static_root),
                        state_dir=str(tmp_path / "state"),
                        user_home=str(tmp_path / "home"),
                        version="0.0.0-test",
                        debugger_port=debugger.port,
                        decky_port=free_port(),
                    ),
                ),
                20,
            )
            assert read_during_the_build == [None]
            assert read_while_running == [(ListedApp("Celeste", status_read=True),)]
        finally:
            await debugger.stop()

    async def test_a_stranded_panel_is_told_what_the_injectors_recovery_holds(
        self, tmp_path, recorder, default_sigterm
    ):
        page = FakePage(
            marker_expression=marker_present_expression(MARKER),
            ready_expression=choose_bundles(decky_is_serving=False).ready_when,
            running_apps=["Celeste"],
        )
        page.marker = True
        page.marker_instance = "an-earlier-backend"
        debugger = FakeDebugger(page)
        await debugger.start()
        debugger.targets = [FakeTarget(id="renderer", title="SharedJSContext")]
        debugger.handlers["Page.enable"] = lambda _params: {}
        told: list[int] = []

        async def while_running() -> None:
            async with asyncio.timeout(10):
                while (port := recorder.port_file.read()) is None or page.app_checks < 1:
                    await asyncio.sleep(0.01)
            client = await WsTestClient.connect(port, "an-earlier-backends-token", session="stranded")
            try:
                opcode, payload = await client.recv_frame()
            finally:
                await client.close()
            assert opcode == OPCODE_CLOSE
            told.append(struct.unpack("!H", payload[:2])[0])
            os.kill(os.getpid(), signal.SIGTERM)

        recorder.while_running = while_running  # type: ignore[method-assign]
        try:
            await asyncio.wait_for(
                _run(
                    tmp_path,
                    recorder,
                    HostStatus(),
                    free_port(),
                    injection=InjectionSetup(
                        static_root=str(tmp_path / "dist"),
                        state_dir=str(tmp_path / "state"),
                        user_home=str(tmp_path / "home"),
                        version="0.0.0-test",
                        debugger_port=debugger.port,
                        decky_port=free_port(),
                    ),
                ),
                20,
            )
            assert told == [CLOSE_STRANDED_PANEL_RELOADS]
        finally:
            await debugger.stop()

    async def test_the_switch_turns_it_off_without_touching_the_rest_of_the_run(
        self, tmp_path, recorder, default_sigterm
    ):
        page = FakePage(
            marker_expression=marker_present_expression(MARKER),
            ready_expression=choose_bundles(decky_is_serving=False).ready_when,
        )
        debugger = FakeDebugger(page)
        await debugger.start()
        debugger.targets = [FakeTarget(id="renderer", title="SharedJSContext")]
        try:
            await asyncio.wait_for(
                _run(
                    tmp_path,
                    recorder,
                    HostStatus(),
                    free_port(),
                    injection=InjectionSetup(
                        static_root=str(tmp_path / "dist"),
                        state_dir=str(tmp_path / "state"),
                        user_home=str(tmp_path / "home"),
                        version="0.0.0-test",
                        override="off",
                        debugger_port=debugger.port,
                        decky_port=free_port(),
                    ),
                ),
                20,
            )
            assert page.evaluated == []
            assert recorder.steps == ["build", "shutdown"]
        finally:
            await debugger.stop()


class TestSteamsDebuggerMarker:
    """The run makes sure Steam will open a debugger at all before it tries to attach.

    ``ensure_debugger_marker`` is held to its own contract in
    ``tests/host/inject/test_machine.py``; what these two pin is that the run
    reaches it exactly where the panel is being loaded and nowhere else.
    """

    @staticmethod
    def _steam_home(tmp_path) -> None:
        (tmp_path / "home" / ".local" / "share" / "Steam").mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _marker(tmp_path):
        return tmp_path / "home" / ".local" / "share" / "Steam" / ".cef-enable-remote-debugging"

    async def test_a_run_that_loads_the_panel_creates_it(self, tmp_path, recorder, default_sigterm):
        self._steam_home(tmp_path)
        page = FakePage(
            marker_expression=marker_present_expression(MARKER),
            ready_expression=choose_bundles(decky_is_serving=False).ready_when,
        )
        debugger = FakeDebugger(page)
        await debugger.start()
        try:
            await asyncio.wait_for(
                _run(
                    tmp_path,
                    recorder,
                    HostStatus(),
                    free_port(),
                    injection=InjectionSetup(
                        static_root=str(tmp_path / "dist"),
                        state_dir=str(tmp_path / "state"),
                        user_home=str(tmp_path / "home"),
                        version="0.0.0-test",
                        debugger_port=debugger.port,
                        decky_port=free_port(),
                    ),
                ),
                20,
            )
        finally:
            await debugger.stop()

        assert self._marker(tmp_path).is_file()

    async def test_the_switch_that_leaves_steam_alone_writes_nothing_into_it(self, tmp_path, recorder, default_sigterm):
        """``off`` means this process touches Steam's directory not at all."""
        self._steam_home(tmp_path)
        page = FakePage(
            marker_expression=marker_present_expression(MARKER),
            ready_expression=choose_bundles(decky_is_serving=False).ready_when,
        )
        debugger = FakeDebugger(page)
        await debugger.start()
        try:
            await asyncio.wait_for(
                _run(
                    tmp_path,
                    recorder,
                    HostStatus(),
                    free_port(),
                    injection=InjectionSetup(
                        static_root=str(tmp_path / "dist"),
                        state_dir=str(tmp_path / "state"),
                        user_home=str(tmp_path / "home"),
                        version="0.0.0-test",
                        override="off",
                        debugger_port=debugger.port,
                        decky_port=free_port(),
                    ),
                ),
                20,
            )
        finally:
            await debugger.stop()

        assert not self._marker(tmp_path).exists()
