"""``EsdeStatusService`` over fakes: what the ES-DE settings section shows, and what it says it could not establish."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest
from fakes.fake_romm_api import FakeRommApi
from fakes.running_loop import running_loop

from domain.retrodeck_folders import FolderRefused, switched_off
from lib.errors import RommApiError, RommConnectionError, RommServerError
from services.esde.hook_scripts import HookScriptsReading
from services.esde.hooks import EsdeHookCall
from services.esde.status import EsdeStatusService, EsdeStatusServiceConfig

_SCRIPTS = "/retrodeck/ES-DE/scripts"
_EVERY_VALUE = [
    "box2d",
    "logo",
    "screenshot",
    "title_screen",
    "box3d",
    "box2d_back",
    "physical",
    "fanart",
    "miximage",
    "video",
    "manual",
]


@dataclass
class Harness:
    service: EsdeStatusService
    settings: dict[str, Any]
    romm: FakeRommApi
    seen: dict[str, Any]


@pytest.fixture
def h(logger) -> Harness:
    settings: dict[str, Any] = {"library_surface": "esde"}
    romm = FakeRommApi()
    seen: dict[str, Any] = {
        "scripts": HookScriptsReading(folder=_SCRIPTS, installed=4, expected=4),
        "last_call": None,
    }

    async def read_scripts() -> HookScriptsReading | FolderRefused:
        return seen["scripts"]

    service = EsdeStatusService(
        config=EsdeStatusServiceConfig(
            settings=settings,
            read_scripts=read_scripts,
            last_call=lambda: seen["last_call"],
            romm_config=romm,
            loop=running_loop(),
            logger=logger,
        )
    )
    return Harness(service, settings, romm, seen)


class TestTheSurface:
    @pytest.mark.parametrize("surface", ["steam", "esde", "both"])
    async def test_it_is_the_one_stored(self, h, surface):
        h.settings["library_surface"] = surface

        assert (await h.service.get_esde_status())["surface"] == surface

    @pytest.mark.parametrize("stored", [None, "nonsense", 3])
    async def test_a_value_naming_no_surface_reads_as_steam(self, h, stored):
        h.settings["library_surface"] = stored

        assert (await h.service.get_esde_status())["surface"] == "steam"


class TestTheScripts:
    async def test_the_folder_and_how_many_are_there(self, h):
        h.seen["scripts"] = HookScriptsReading(folder=_SCRIPTS, installed=3, expected=4)

        status = await h.service.get_esde_status()

        assert status["scripts"] == {"known": True, "folder": _SCRIPTS, "installed": 3, "expected": 4}

    async def test_a_folder_not_known_says_why(self, h):
        refusal = switched_off("Games in ES-DE")
        h.seen["scripts"] = refusal

        status = await h.service.get_esde_status()

        assert status["scripts"] == {"known": False, "reason": refusal.reason, "message": refusal.message}


class TestTheLastCall:
    async def test_never_is_none(self, h):
        assert (await h.service.get_esde_status())["last_call"] is None

    async def test_the_hook_and_when(self, h):
        h.seen["last_call"] = EsdeHookCall(hook="prepare", at="2026-10-25T12:00:00+00:00")

        status = await h.service.get_esde_status()

        assert status["last_call"] == {"hook": "prepare", "at": "2026-10-25T12:00:00+00:00"}


class TestWhatRommDoesNotCollect:
    async def test_romm_s_default_leaves_eight_folders_without_media(self, h):
        status = await h.service.get_esde_status()

        assert status["media_not_collected"] == {
            "established": True,
            "folders": [
                "marquees",
                "titlescreens",
                "3dboxes",
                "backcovers",
                "physicalmedia",
                "fanart",
                "miximages",
                "videos",
            ],
        }

    async def test_everything_collected_is_an_empty_list_that_was_established(self, h):
        h.romm.scan_media = list(_EVERY_VALUE)

        status = await h.service.get_esde_status()

        assert status["media_not_collected"] == {"established": True, "folders": []}

    @pytest.mark.parametrize(
        "error",
        [RommConnectionError("refused"), RommServerError("HTTP 502"), RommApiError("Unexpected response")],
    )
    async def test_a_read_that_failed_is_not_established_never_nothing_missing(self, h, error):
        h.romm.get_scan_media_side_effect = error

        status = await h.service.get_esde_status()

        not_collected = status["media_not_collected"]
        assert not_collected["established"] is False
        assert "folders" not in not_collected
        assert isinstance(not_collected["message"], str)
        assert not_collected["message"]

    async def test_the_keys_are_exactly_these(self, h):
        assert set(await h.service.get_esde_status()) == {"surface", "scripts", "last_call", "media_not_collected"}
