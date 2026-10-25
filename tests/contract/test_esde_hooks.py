"""Contract tests for ES-DE's hooks through the real wiring: what a script's call does to a game of Tender's.

The hooks are called as the host calls them, through ``main.EsdeHooks`` over
the hook service ``wire_services`` built, with the arguments a script sends:
the path as ES-DE escapes it (``fakes/esde_events.py``) and ``escaped=1``.
The stand-ins are placed by a real library sync under the ``esde`` surface, in
a RetroDECK seeded under ``tmp_path``; only the RomM transport is the fake, and
so is the question whether ES-DE runs.
"""

from __future__ import annotations

import asyncio
import os
from typing import Any

import pytest
from fakes.esde_events import escaped_path

from domain.sync_run_kind import SyncRunKind
from main import EsdeHooks, esde_hook_routes

pytestmark = pytest.mark.usefixtures("seeded_retrodeck")

_PAYLOAD = b"GBA-ROM!"
_MP4 = b"\x00\x00\x00\x18ftypisom" + bytes(52)


def _hooks(harness) -> dict[str, Any]:
    return esde_hook_routes(EsdeHooks(harness.app.services.esde_hook_service))


def _serve_file(harness, rom_id: int, fs_name: str) -> None:
    """A GBA platform RomM lists, and one plain file on it that it serves as *_PAYLOAD*."""
    harness.romm.platforms.append({"id": 1, "name": "GBA", "slug": "gba", "rom_count": 1})
    harness.settings["enabled_platforms"] = {"1": True}
    harness.romm.roms[rom_id] = {
        "id": rom_id,
        "name": f"Game {rom_id}",
        "fs_name": fs_name,
        "fs_size_bytes": len(_PAYLOAD),
        "platform_id": 1,
        "platform_name": "GBA",
        "platform_slug": "gba",
        "platform_fs_slug": "gba",
        "has_simple_single_file": True,
        "updated_at": "2026-01-01T00:00:00+00:00",
    }
    harness.romm.download_payloads[f"rom:{rom_id}:{fs_name}"] = _PAYLOAD


async def _sync_esde(harness) -> None:
    """One apply run over the fake RomM under the ``esde`` surface, which places the stand-ins."""
    harness.settings["library_surface"] = "esde"
    sync = harness.app.services.sync_service
    assert sync._box.try_begin_run("run-1", kind=SyncRunKind.APPLY) is True
    await sync._orchestrator._do_sync_per_unit()


async def _drain() -> None:
    """Await every task the sync, a download or the media service started."""
    for _ in range(20):
        pending = [t for t in asyncio.all_tasks() if t is not asyncio.current_task() and not t.done()]
        if not pending:
            return
        await asyncio.gather(*pending, return_exceptions=True)
        await asyncio.sleep(0)


def _installed_path(harness, rom_id: int) -> str | None:
    with harness.uow_factory() as uow:
        install = uow.rom_installs.get(rom_id)
    return None if install is None else install.file_path


def _emitted(harness, event: str) -> list[dict[str, Any]]:
    return [call.args[1] for call in harness.emit.await_args_list if call.args[0] == event]


@pytest.fixture
async def stand_in(harness) -> str:
    """The path of the stand-in a sync placed for ROM 10, a game not downloaded yet."""
    _serve_file(harness, 10, "Game (USA) [!].gba")
    await _sync_esde(harness)
    path = os.path.join(harness.roms_root, "gba", "Game (USA) [!].gba")
    assert harness.app.services.esde_stand_in_service.do_stand_in_at(path) == 10
    return path


async def test_prepare_on_a_stand_in_downloads_the_game_to_its_path_and_answers_success(harness, stand_in):
    answer = await _hooks(harness)["prepare"]({"path": escaped_path(stand_in), "escaped": "1"})

    assert answer["success"] is True
    assert (answer["rom_id"], answer["downloaded"], answer["listed"]) == (10, True, True)
    assert not os.path.islink(stand_in)
    with open(stand_in, "rb") as handle:
        assert handle.read() == _PAYLOAD
    assert _installed_path(harness, 10) == stand_in
    stages = [frame["stage"] for frame in _emitted(harness, "esde_launch")]
    assert stages == ["downloading", "saves"]


async def test_finish_after_a_start_ends_the_session_and_says_what_it_did(harness, stand_in):
    hooks = _hooks(harness)
    await hooks["prepare"]({"path": escaped_path(stand_in), "escaped": "1"})

    answer = await hooks["finish"]({"path": escaped_path(stand_in), "escaped": "1"})

    assert answer == {"success": True, "rom_id": 10}
    [said] = _emitted(harness, "esde_session")
    assert said["rom_id"] == 10
    assert set(said["result"]) == {"total_seconds", "sync", "migration"}


async def test_a_download_that_fails_answers_the_failure_shape_and_puts_the_stand_in_back(harness, stand_in):
    harness.romm.download_rom_content_side_effect = ConnectionResetError("reset mid-transfer")

    answer = await _hooks(harness)["prepare"]({"path": escaped_path(stand_in), "escaped": "1"})

    assert answer["success"] is False
    assert answer["reason"] == "download_failed"
    assert isinstance(answer["message"], str)
    assert harness.app.services.esde_stand_in_service.do_stand_in_at(stand_in) == 10
    assert _installed_path(harness, 10) is None
    [said] = [frame for frame in _emitted(harness, "esde_launch") if frame["stage"] == "not_downloaded"]
    assert said["rom_id"] == 10


@pytest.mark.parametrize("hook", ["prepare", "finish", "select"])
async def test_a_path_that_is_no_game_of_tenders_answers_the_failure_shape(harness, hook):
    elsewhere = os.path.join(harness.roms_root, "gba", "Not Tenders.gba")
    os.makedirs(os.path.dirname(elsewhere), exist_ok=True)
    with open(elsewhere, "wb") as handle:
        handle.write(b"theirs")
    args = {"path": elsewhere} if hook == "select" else {"path": elsewhere, "escaped": "0"}

    answer = await _hooks(harness)[hook](args)

    assert answer["success"] is False
    assert answer["reason"] == "not_a_tender_game"
    assert isinstance(answer["message"], str)


async def test_startup_answers_with_nothing_held_back(harness):
    assert await _hooks(harness)["startup"]({}) == {"success": True, "finished": True}


async def test_select_on_a_stand_in_fetches_the_game_s_video_and_links_it(harness):
    _serve_file(harness, 10, "Game (USA) [!].gba")
    harness.romm.roms[10]["path_video"] = "roms/1/10/video/video.mp4"
    harness.romm.media_files["/assets/romm/resources/roms/1/10/video/video.mp4"] = _MP4
    await _sync_esde(harness)
    await _drain()
    stand_in = os.path.join(harness.roms_root, "gba", "Game (USA) [!].gba")
    video = os.path.join(harness.retrodeck_home, "ES-DE", "downloaded_media", "gba", "videos", "Game (USA) [!].mp4")
    assert not os.path.lexists(video)

    answer = await _hooks(harness)["select"]({"path": stand_in})
    await _drain()

    assert answer == {"success": True, "rom_id": 10}
    assert os.path.islink(video)
    with open(video, "rb") as handle:
        assert handle.read() == _MP4


async def test_startup_writes_the_gamelist_a_sync_held_back_while_es_de_ran(harness):
    _serve_file(harness, 10, "Game (USA) [!].gba")
    harness.esde_running.running = True
    await _sync_esde(harness)
    await _drain()
    gamelist = os.path.join(harness.retrodeck_home, "ES-DE", "gamelists", "gba", "gamelist.xml")
    assert not os.path.exists(gamelist)

    answer = await _hooks(harness)["startup"]({})

    assert answer == {"success": True, "finished": True}
    with open(gamelist, encoding="utf-8") as handle:
        written = handle.read()
    assert "<path>./Game (USA) [!].gba</path>" in written
    assert "<name>Game 10</name>" in written
