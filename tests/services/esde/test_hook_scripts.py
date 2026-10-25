"""``EsdeHookScriptService`` over an in-memory store: the scripts there while the surface writes ES-DE, else gone."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest
from fakes.fake_retrodeck_folders import FakeRetroDeckFolders
from fakes.running_loop import running_loop

from domain.esde_hook_scripts import EsdeHookAddress, hook_scripts, script_path
from domain.retrodeck_folders import FolderRefused, switched_off
from services.esde import EsdeHookScriptService, EsdeHookScriptServiceConfig
from services.esde.hook_scripts import HookScriptsReading

_ADDRESS = EsdeHookAddress(port_file="/data/esde-hooks/port", header_file="/data/esde-hooks/header", route_prefix="/h/")
_SCRIPTS = "/retrodeck/ES-DE/scripts"


@dataclass
class _Store:
    """Scripts by path; ``theirs`` are paths holding something not Tender's, which nothing may change."""

    scripts: dict[str, str] = field(default_factory=dict)
    theirs: set[str] = field(default_factory=set)
    failing: set[str] = field(default_factory=set)

    def install(self, path: str, text: str) -> bool:
        if path in self.failing:
            raise OSError("read-only")
        if path in self.theirs:
            return False
        self.scripts[path] = text
        return True

    def remove(self, path: str) -> bool:
        if path in self.failing:
            raise OSError("read-only")
        return self.scripts.pop(path, None) is not None

    def holds(self, path: str) -> bool:
        return path in self.scripts


@dataclass
class Harness:
    service: EsdeHookScriptService
    store: _Store
    folders: FakeRetroDeckFolders
    settings: dict[str, Any]


@pytest.fixture
def h(logger) -> Harness:
    store = _Store()
    folders = FakeRetroDeckFolders(roms="/retrodeck/roms", home="/retrodeck")
    settings: dict[str, Any] = {"library_surface": "esde"}
    service = EsdeHookScriptService(
        config=EsdeHookScriptServiceConfig(
            store=store,
            retrodeck_folders=folders,
            address=_ADDRESS,
            settings=settings,
            loop=running_loop(),
            logger=logger,
            log_debug=lambda msg: None,
        )
    )
    return Harness(service, store, folders, settings)


def _expected() -> dict[str, str]:
    return {script_path(_SCRIPTS, script.event): script.text for script in hook_scripts(_ADDRESS)}


class TestAtTheBackendsStart:
    @pytest.mark.parametrize("surface", ["esde", "both"])
    def test_a_surface_that_writes_es_de_gets_every_script(self, h, surface):
        h.settings["library_surface"] = surface

        h.service.install_at_start()

        assert h.store.scripts == _expected()

    @pytest.mark.parametrize("surface", ["steam", None, "nonsense"])
    def test_any_other_surface_gets_none_and_loses_none(self, h, surface):
        h.settings["library_surface"] = surface
        h.store.scripts["/elsewhere"] = "kept"

        h.service.install_at_start()

        assert h.store.scripts == {"/elsewhere": "kept"}

    def test_es_des_folders_refused_write_nothing_and_raise_nothing(self, h):
        h.folders.download_refusal = switched_off("Games in ES-DE")

        h.service.install_at_start()

        assert h.store.scripts == {}

    def test_one_script_that_cannot_be_written_leaves_the_others_written(self, h):
        failing = script_path(_SCRIPTS, "game-start")
        h.store.failing.add(failing)

        h.service.install_at_start()

        assert set(h.store.scripts) == set(_expected()) - {failing}

    def test_a_name_holding_something_of_the_users_is_left(self, h):
        theirs = script_path(_SCRIPTS, "game-end")
        h.store.theirs.add(theirs)

        h.service.install_at_start()

        assert theirs not in h.store.scripts


class TestWhenTheSurfaceIsStored:
    @pytest.mark.parametrize("surface", ["esde", "both"])
    async def test_a_surface_that_writes_es_de_writes_every_script(self, h, surface):
        await h.service.surface_stored(surface)

        assert h.store.scripts == _expected()

    async def test_steam_removes_every_script_of_tenders(self, h):
        await h.service.surface_stored("esde")

        await h.service.surface_stored("steam")

        assert h.store.scripts == {}

    async def test_steam_leaves_whatever_is_not_tenders(self, h):
        h.store.scripts["/retrodeck/ES-DE/scripts/game-start/01-mine.sh"] = "mine"

        await h.service.surface_stored("steam")

        assert h.store.scripts == {"/retrodeck/ES-DE/scripts/game-start/01-mine.sh": "mine"}

    async def test_a_removal_that_fails_is_logged_and_the_rest_still_go(self, h):
        await h.service.surface_stored("esde")
        failing = script_path(_SCRIPTS, "startup")
        h.store.failing.add(failing)

        await h.service.surface_stored("steam")

        assert set(h.store.scripts) == {failing}

    async def test_es_des_folders_refused_change_nothing(self, h):
        await h.service.surface_stored("esde")
        h.folders.download_refusal = switched_off("Games in ES-DE")

        await h.service.surface_stored("steam")

        assert h.store.scripts == _expected()


class TestWhatIsThere:
    async def test_every_script_of_tenders_there_reads_as_all_installed(self, h):
        await h.service.surface_stored("esde")

        reading = await h.service.read_installed()

        assert reading == HookScriptsReading(folder=_SCRIPTS, installed=len(_expected()), expected=len(_expected()))

    async def test_none_there_reads_as_none_installed(self, h):
        reading = await h.service.read_installed()

        assert reading == HookScriptsReading(folder=_SCRIPTS, installed=0, expected=len(_expected()))

    async def test_a_name_holding_the_users_own_script_is_not_counted(self, h):
        h.store.theirs.add(script_path(_SCRIPTS, "game-start"))
        await h.service.surface_stored("esde")

        reading = await h.service.read_installed()

        assert isinstance(reading, HookScriptsReading)
        assert reading.installed == len(_expected()) - 1

    async def test_es_des_folders_refused_answer_the_refusal(self, h):
        refusal = switched_off("Games in ES-DE")
        h.folders.download_refusal = refusal

        reading = await h.service.read_installed()

        assert isinstance(reading, FolderRefused)
        assert reading.reason == refusal.reason
