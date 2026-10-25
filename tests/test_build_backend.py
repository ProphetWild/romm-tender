"""What the process entry hands the host: the built application, repaired, behind the endpoints, and its tokens."""

from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock

from _factories import _make_services_bundle

import main
from domain.app_directories import AppDirectories
from domain.esde_hook_scripts import EsdeHookAddress
from domain.update_release import UpdateSource
from host import EventSink, HostStatus
from host.dispatch import route_names
from host.hooks import ESDE_HOOKS_PREFIX, HOOK_HEADER_FILENAME, HOOK_PORT_FILENAME
from host.logging_setup import LOG_FILENAME
from main import Endpoints, build_backend

if TYPE_CHECKING:
    from collections.abc import Callable

    import pytest

LOGGER = logging.getLogger("test_build_backend")


class _StubApplication:
    """An ``Application`` in shape, recording what the build does with it."""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.services = _make_services_bundle(
            connection_service=MagicMock(test_connection=AsyncMock(return_value={"success": True})),
        )
        self.user_agent = "romm-tender/0.0.0-stub"

    def run_startup_repairs(self, report_failure: Callable[[str], None]) -> None:
        self.calls.append("run_startup_repairs")
        report_failure("prune_orphaned_cover_cache")

    async def shutdown(self) -> None:
        self.calls.append("shutdown")


def _directories_at(tmp_path) -> AppDirectories:
    return AppDirectories(
        config_dir=str(tmp_path / "config"),
        data_dir=str(tmp_path / "data"),
        cache_dir=str(tmp_path / "cache"),
        state_dir=str(tmp_path / "state"),
        runtime_dir=str(tmp_path / "run"),
        code_dir=str(tmp_path / "code"),
        bin_dir=str(tmp_path / "home" / ".local" / "bin"),
    )


_UPDATE_SOURCE = UpdateSource(release_api="http://127.0.0.1:9/releases/latest", installed_program=False)
_INSTALLER_ENVIRONMENT = (("TENDER_DATA_DIR", "/data"),)
_HOOK_ADDRESS = EsdeHookAddress(
    port_file="/data/esde-hooks/port", header_file="/data/esde-hooks/header", route_prefix="/h/"
)


class TestBuildBackend:
    @staticmethod
    async def _build(tmp_path, monkeypatch: pytest.MonkeyPatch):
        app = _StubApplication()
        asked: list[dict[str, Any]] = []

        def stub_build_application(**kwargs: Any) -> _StubApplication:
            asked.append(kwargs)
            return app

        monkeypatch.setattr(main, "build_application", stub_build_application)
        status = HostStatus()
        events = EventSink(LOGGER)
        built = await build_backend(
            directories=_directories_at(tmp_path),
            update_source=_UPDATE_SOURCE,
            installer_environment=_INSTALLER_ENVIRONMENT,
            user_home=str(tmp_path / "home"),
            logger=LOGGER,
            status=status,
            events=events,
            esde_hook_address=_HOOK_ADDRESS,
        )
        return built, app, status, events, asked

    async def test_it_builds_the_application_on_the_running_loop_emitting_through_the_sink(
        self, tmp_path, monkeypatch: pytest.MonkeyPatch
    ):
        _built, _app, status, events, asked = await self._build(tmp_path, monkeypatch)
        panel_connected = asked[0].pop("panel_connected")

        assert asked == [
            {
                "directories": _directories_at(tmp_path),
                "update_source": _UPDATE_SOURCE,
                "installer_environment": _INSTALLER_ENVIRONMENT,
                "user_home": str(tmp_path / "home"),
                "logger": LOGGER,
                "loop": asyncio.get_running_loop(),
                "emit": events.emit,
                "steam": status.steam,
                "esde_hook_address": _HOOK_ADDRESS,
            }
        ]
        assert panel_connected() is False

        async def send(text: str) -> bool:
            return True

        events.attach(send)
        assert panel_connected() is True

    async def test_a_failing_repair_is_recorded_on_the_host_status(self, tmp_path, monkeypatch: pytest.MonkeyPatch):
        _built, app, status, _events, _asked = await self._build(tmp_path, monkeypatch)

        assert app.calls == ["run_startup_repairs"]
        assert status.failed_startup_steps == ["prune_orphaned_cover_cache"]

    async def test_the_host_shuts_down_the_application(self, tmp_path, monkeypatch: pytest.MonkeyPatch):
        built, app, _status, _events, _asked = await self._build(tmp_path, monkeypatch)
        app.calls.clear()

        await built.shutdown()

        assert app.calls == ["shutdown"]

    async def test_it_answers_under_the_application_s_identity(self, tmp_path, monkeypatch: pytest.MonkeyPatch):
        built, app, _status, _events, _asked = await self._build(tmp_path, monkeypatch)

        assert built.server_identity == app.user_agent

    async def test_calls_reach_the_endpoints_over_the_application_and_the_host_status(
        self, tmp_path, monkeypatch: pytest.MonkeyPatch
    ):
        built, _app, _status, _events, _asked = await self._build(tmp_path, monkeypatch)

        service_answer = json.loads(await built.dispatcher.dispatch(1, "test_connection", []))
        host_answer = json.loads(await built.dispatcher.dispatch(2, "get_host_status", []))

        assert built.dispatcher.method_names == route_names(Endpoints)
        assert service_answer["result"] == {"success": True}
        assert host_answer["result"]["failed_startup_steps"] == ["prune_orphaned_cover_cache"]

    async def test_every_hook_reaches_the_application_s_hook_service(self, tmp_path, monkeypatch: pytest.MonkeyPatch):
        built, app, _status, _events, _asked = await self._build(tmp_path, monkeypatch)
        service = app.services.esde_hook_service
        service.prepare = AsyncMock(return_value={"success": True, "rom_id": 7})
        service.finish = AsyncMock(return_value={"success": True, "rom_id": 7})
        service.select = AsyncMock(return_value={"success": True, "rom_id": 7})
        service.startup = AsyncMock(return_value={"success": True, "finished": True})
        assert built.esde_hooks is not None

        answer = await built.esde_hooks["prepare"]({"path": "/roms/a b.sfc", "escaped": "1"})
        await built.esde_hooks["finish"]({"path": "/roms/a.sfc", "escaped": "0"})
        await built.esde_hooks["select"]({"path": "/roms/a.sfc"})
        await built.esde_hooks["startup"]({})

        assert answer == {"success": True, "rom_id": 7}
        service.prepare.assert_awaited_once_with("/roms/a b.sfc", True)
        service.finish.assert_awaited_once_with("/roms/a.sfc", False)
        service.select.assert_awaited_once_with("/roms/a.sfc")
        service.startup.assert_awaited_once_with()


class TestRun:
    """The start ``run`` hands the host: both tokens minted, both kept out of the log file, the hook files' place."""

    @staticmethod
    def _start(tmp_path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
        for name, directory in (
            ("TENDER_CONFIG_DIR", "config"),
            ("TENDER_DATA_DIR", "data"),
            ("TENDER_CACHE_DIR", "cache"),
            ("TENDER_STATE_DIR", "state"),
            ("TENDER_CODE_DIR", "code"),
            ("TENDER_BIN_DIR", "bin"),
            ("XDG_RUNTIME_DIR", "run"),
        ):
            monkeypatch.setenv(name, str(tmp_path / directory))
        handed: dict[str, Any] = {}

        async def recording_run_backend(**kwargs: Any) -> None:
            handed.update(kwargs)

        monkeypatch.setattr(main, "run_backend", recording_run_backend)
        assert main.run() == 0
        return handed

    def test_it_hands_the_host_a_hook_token_of_its_own(self, tmp_path, monkeypatch, restore_root_logger):
        handed = self._start(tmp_path, monkeypatch)

        assert len(handed["hook_token"]) >= 32
        assert handed["hook_token"] != handed["token"]

    def test_every_start_mints_a_fresh_one(self, tmp_path, monkeypatch, restore_root_logger):
        first = self._start(tmp_path, monkeypatch)["hook_token"]
        second = self._start(tmp_path, monkeypatch)["hook_token"]

        assert first != second

    def test_the_hook_files_go_into_the_data_root(self, tmp_path, monkeypatch, restore_root_logger):
        handed = self._start(tmp_path, monkeypatch)

        assert handed["hook_files_dir"] == os.path.join(str(tmp_path / "data"), "esde-hooks")

    def test_the_scripts_read_the_hook_files_the_host_writes_and_call_its_hook_route(
        self, tmp_path, monkeypatch, restore_root_logger
    ):
        handed = self._start(tmp_path, monkeypatch)

        address = handed["build"].keywords["esde_hook_address"]
        assert address == EsdeHookAddress(
            port_file=os.path.join(handed["hook_files_dir"], HOOK_PORT_FILENAME),
            header_file=os.path.join(handed["hook_files_dir"], HOOK_HEADER_FILENAME),
            route_prefix=ESDE_HOOKS_PREFIX,
        )

    def test_neither_token_reaches_the_log_file(self, tmp_path, monkeypatch, restore_root_logger):
        handed = self._start(tmp_path, monkeypatch)

        logging.getLogger().warning(f"a line carrying {handed['token']} and {handed['hook_token']}")

        written = (tmp_path / "state" / LOG_FILENAME).read_text(encoding="utf-8")
        assert "a line carrying" in written
        assert handed["token"] not in written
        assert handed["hook_token"] not in written
