"""Contract tests for ``get_esde_status``: what the ES-DE settings section reads, through the real wiring.

Driven over the real ``bootstrap()``, the real script service and its adapter
in a RetroDECK seeded under ``tmp_path``, and the real hook service; only the
RomM transport is the fake.
"""

from __future__ import annotations

import os

import pytest

from lib.errors import RommConnectionError
from main import EsdeHooks, esde_hook_routes

_ROMM_DEFAULT_LEAVES_OUT = [
    "marquees",
    "titlescreens",
    "3dboxes",
    "backcovers",
    "physicalmedia",
    "fanart",
    "miximages",
    "videos",
]


def _scripts_folder(harness) -> str:
    return os.path.realpath(os.path.join(harness.retrodeck_home, "ES-DE", "scripts"))


@pytest.mark.usefixtures("seeded_retrodeck")
async def test_under_esde_the_scripts_are_installed_es_de_never_called_and_romm_s_default_leaves_eight_out(harness):
    assert await harness.endpoints.set_library_surface("esde") == {"success": True}

    status = await harness.endpoints.get_esde_status()

    assert set(status) == {"surface", "scripts", "last_call", "media_not_collected"}
    assert status["surface"] == "esde"
    scripts = status["scripts"]
    assert set(scripts) == {"known", "folder", "installed", "expected"}
    assert scripts["known"] is True
    assert os.path.realpath(scripts["folder"]) == _scripts_folder(harness)
    assert scripts["expected"] == 4
    assert scripts["installed"] == 4
    assert status["last_call"] is None
    assert status["media_not_collected"] == {"established": True, "folders": _ROMM_DEFAULT_LEAVES_OUT}


@pytest.mark.usefixtures("seeded_retrodeck")
async def test_under_steam_none_of_the_scripts_is_there(harness):
    status = await harness.endpoints.get_esde_status()

    assert status["surface"] == "steam"
    assert status["scripts"]["known"] is True
    assert status["scripts"]["installed"] == 0


@pytest.mark.usefixtures("seeded_retrodeck")
async def test_the_last_call_es_de_made_is_named_with_when(harness):
    await esde_hook_routes(EsdeHooks(harness.app.services.esde_hook_service))["startup"]({})

    status = await harness.endpoints.get_esde_status()

    assert status["last_call"] == {"hook": "startup", "at": harness.clock.now().isoformat()}


@pytest.mark.usefixtures("seeded_retrodeck")
async def test_romm_unreachable_leaves_what_it_collects_not_established_never_nothing_missing(harness):
    harness.romm.get_scan_media_side_effect = RommConnectionError("refused")

    status = await harness.endpoints.get_esde_status()

    not_collected = status["media_not_collected"]
    assert set(not_collected) == {"established", "message"}
    assert not_collected["established"] is False
    assert not_collected["message"]


async def test_without_retrodeck_where_the_scripts_go_is_not_known_and_says_why(harness):
    status = await harness.endpoints.get_esde_status()

    scripts = status["scripts"]
    assert set(scripts) == {"known", "reason", "message"}
    assert scripts["known"] is False
    assert isinstance(scripts["reason"], str)
    assert scripts["reason"]
    assert scripts["message"]
