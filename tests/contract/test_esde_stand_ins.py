"""Contract tests for ES-DE's stand-ins around a download, an uninstall, an adoption and the game page.

Driven frontend-shaped over the real ``bootstrap()`` + SQLite, the real stand-in
service and its adapters, and a RetroDECK seeded under ``tmp_path``, whose ROM
folders are the real resolver's answer; only the RomM transport is the fake.
A stand-in is placed by a real library sync under the ``esde`` surface, or, for
a sibling group, which the fake RomM's listing cannot build, by the stand-in
service's own pass.
"""

from __future__ import annotations

import asyncio
import io
import json
import os
import urllib.parse
import zipfile
from typing import Any

import pytest

from domain.rom_files import safe_local_file_name
from domain.sync_run_kind import SyncRunKind
from domain.work_unit import WorkUnit

from ._seed import seed_es_systems, seed_group_member

pytestmark = pytest.mark.usefixtures("seeded_retrodeck")

_GROUP = "game-group"

_GBA_AND_PSX_XML = """\
<?xml version="1.0"?>
<systemList>
  <system>
    <name>gba</name>
    <path>%ROMPATH%/gba</path>
    <command label="mGBA">%EMULATOR_RETROARCH% -L %CORE_RETROARCH%/mgba_libretro.so %ROM%</command>
  </system>
  <system>
    <name>psx</name>
    <path>%ROMPATH%/psx</path>
    <extension>.cue .CUE .bin .BIN .m3u .M3U</extension>
    <command label="Beetle PSX">%EMULATOR_RETROARCH% -L %CORE_RETROARCH%/mednafen_psx_libretro.so %ROM%</command>
  </system>
</systemList>
"""

_TWO_DISCS = {
    "Game (Disc 1).cue": b'FILE "Game (Disc 1).bin" BINARY\n',
    "Game (Disc 1).bin": b"\x00" * 900,
    "Game (Disc 2).cue": b'FILE "Game (Disc 2).bin" BINARY\n',
    "Game (Disc 2).bin": b"\x00" * 900,
}


def _platform(harness, platform_id: int, slug: str) -> None:
    harness.romm.platforms.append({"id": platform_id, "name": slug.upper(), "slug": slug, "rom_count": 1})
    harness.settings["enabled_platforms"] = {**harness.settings.get("enabled_platforms", {}), str(platform_id): True}


def _serve_file(harness, rom_id: int, fs_name: str, payload: bytes = b"GBA-ROM!") -> dict[str, Any]:
    """A single plain GBA file RomM lists, serves the detail of, and transfers as *payload*."""
    rom = {
        "id": rom_id,
        "name": f"Game {rom_id}",
        "fs_name": fs_name,
        "fs_size_bytes": len(payload),
        "platform_id": 1,
        "platform_name": "GBA",
        "platform_slug": "gba",
        "platform_fs_slug": "gba",
        "has_simple_single_file": True,
        "updated_at": "2026-01-01T00:00:00+00:00",
    }
    harness.romm.roms[rom_id] = rom
    harness.romm.download_payloads[f"rom:{rom_id}:{fs_name}"] = payload
    return rom


def _serve_discs(harness, rom_id: int) -> dict[str, Any]:
    """A two-disc PSX game RomM serves as a folder, transferred as the zip of its files."""
    files = [
        {"file_name": name, "file_path": "romm/library/psx/Game", "file_size_bytes": len(data)}
        for name, data in _TWO_DISCS.items()
    ]
    rom = {
        "id": rom_id,
        "name": "Final Fantasy VII",
        "fs_name": "Game",
        "fs_name_no_ext": "Final Fantasy VII",
        "full_path": "romm/library/psx/Game",
        "fs_size_bytes": sum(len(data) for data in _TWO_DISCS.values()),
        "platform_id": 2,
        "platform_name": "PSX",
        "platform_slug": "psx",
        "platform_fs_slug": "psx",
        "has_multiple_files": True,
        "files": files,
        "updated_at": "2026-01-01T00:00:00+00:00",
    }
    payload = io.BytesIO()
    with zipfile.ZipFile(payload, "w") as archive:
        for name, data in _TWO_DISCS.items():
            archive.writestr(urllib.parse.quote(name), data)
    harness.romm.roms[rom_id] = rom
    harness.romm.download_payloads[f"rom:{rom_id}:{safe_local_file_name(rom).name}"] = payload.getvalue()
    return rom


async def _sync(harness, run_id: str = "run-1") -> None:
    """One apply run over the fake RomM, as the panel's sync starts it, run to its end."""
    sync = harness.app.services.sync_service
    assert sync._box.try_begin_run(run_id, kind=SyncRunKind.APPLY) is True
    await sync._orchestrator._do_sync_per_unit()


async def _drain() -> None:
    """Await the download tasks ``start_download`` spawned."""
    for _ in range(6):
        pending = [t for t in asyncio.all_tasks() if t is not asyncio.current_task() and not t.done()]
        if not pending:
            return
        await asyncio.gather(*pending, return_exceptions=True)


async def _download(harness, rom_id: int) -> dict[str, Any]:
    result = await harness.endpoints.start_download(rom_id, False, None, None, False)
    await _drain()
    return result


def _stands_for(harness, path: str) -> int | None:
    """The game a stand-in at *path* stands in for, read off the link itself; ``None`` for anything else."""
    return harness.app.services.esde_stand_in_service.do_stand_in_at(path)


def _installed_path(harness, rom_id: int) -> str | None:
    with harness.uow_factory() as uow:
        install = uow.rom_installs.get(rom_id)
    return None if install is None else install.file_path


def _tree(root: str) -> dict[str, str]:
    """Every entry below *root*, each as what it is: a link with its target, a folder, or a file with its bytes."""
    found: dict[str, str] = {}
    for dirpath, dirs, names in os.walk(root):
        for name in dirs + names:
            path = os.path.join(dirpath, name)
            if os.path.islink(path):
                found[path] = f"link -> {os.readlink(path)}"
            elif os.path.isdir(path):
                found[path] = "folder"
            else:
                with open(path, "rb") as handle:
                    found[path] = handle.read().hex()
    return found


# ── (a), (b) A download lands at its stand-in's path ───────────────────────


async def test_a_single_file_lands_at_its_stand_in_s_path(harness):
    harness.settings["library_surface"] = "esde"
    _platform(harness, 1, "gba")
    _serve_file(harness, 10, "Game (USA).gba")
    path = os.path.join(harness.roms_root, "gba", "Game (USA).gba")
    await _sync(harness)
    assert _stands_for(harness, path) == 10

    result = await _download(harness, 10)

    assert result == {"success": True, "message": "Download started"}
    assert not os.path.islink(path)
    with open(path, "rb") as handle:
        assert handle.read() == b"GBA-ROM!"
    assert _installed_path(harness, 10) == path


async def test_a_multi_file_game_lands_as_the_collapsed_folder_at_its_stand_in_s_path(harness):
    seed_es_systems(harness, _GBA_AND_PSX_XML)
    harness.settings["library_surface"] = "esde"
    _platform(harness, 2, "psx")
    _serve_discs(harness, 20)
    folder = os.path.join(harness.roms_root, "psx", "Final Fantasy VII.m3u")
    await _sync(harness)
    assert _stands_for(harness, folder) == 20
    assert os.path.islink(folder)

    result = await _download(harness, 20)

    assert result["success"] is True
    assert not os.path.islink(folder)
    assert os.path.isdir(folder)
    assert _installed_path(harness, 20) == os.path.join(folder, "Final Fantasy VII.m3u")
    assert sorted(os.listdir(os.path.join(harness.roms_root, "psx"))) == ["Final Fantasy VII.m3u"]


# ── (c) A sibling's stand-in is no file the user placed ─────────────────────


async def _place_sibling_stand_in(harness) -> str:
    """Versions 30 (the group's representative) and 31 of one game; 30's stand-in placed by the real pass."""
    harness.settings["library_surface"] = "esde"
    seed_group_member(harness, 30, group_key=_GROUP, shortcut_app_id=3030, name="Game (Europe).gba")
    seed_group_member(harness, 31, group_key=_GROUP, shortcut_app_id=3131, name="Game (USA).gba")
    europe = _serve_file(harness, 30, "Game (Europe).gba")
    _serve_file(harness, 31, "Game (USA).gba", payload=b"USA-ROM!")
    stand_ins = harness.app.services.esde_stand_in_service
    stand_ins.begin_run("esde")
    await stand_ins.place(WorkUnit(type="platform", id=1, name="GBA", slug="gba", rom_count=2), [europe], {30})
    await stand_ins.settle()
    path = os.path.join(harness.roms_root, "gba", "Game (Europe).gba")
    assert _stands_for(harness, path) == 30
    return path


async def test_a_sibling_s_stand_in_is_no_adoption_candidate_on_the_game_page(harness):
    await _place_sibling_stand_in(harness)

    detail = await harness.endpoints.get_cached_game_detail(3131)

    assert detail["found"] is True
    assert detail["adoption_candidate_present"] is False
    assert detail["target_path_occupied"] is False


async def test_a_sibling_s_stand_in_does_not_refuse_the_download(harness):
    sibling = await _place_sibling_stand_in(harness)

    result = await _download(harness, 31)

    assert result == {"success": True, "message": "Download started"}
    usa = os.path.join(harness.roms_root, "gba", "Game (USA).gba")
    with open(usa, "rb") as handle:
        assert handle.read() == b"USA-ROM!"
    assert _stands_for(harness, sibling) == 30


async def test_the_game_s_own_stand_in_is_no_occupied_target_on_its_page(harness):
    harness.settings["library_surface"] = "esde"
    seed_group_member(harness, 30, group_key=_GROUP, shortcut_app_id=3030, name="Game (Europe).gba")
    europe = _serve_file(harness, 30, "Game (Europe).gba")
    stand_ins = harness.app.services.esde_stand_in_service
    stand_ins.begin_run("esde")
    await stand_ins.place(WorkUnit(type="platform", id=1, name="GBA", slug="gba", rom_count=1), [europe], {30})
    await stand_ins.settle()

    detail = await harness.endpoints.get_cached_game_detail(3030)

    assert detail["target_path_occupied"] is False
    assert detail["adoption_candidate_present"] is False


# ── (d) A download that fails leaves the stand-in in place ──────────────────


async def test_a_download_that_fails_puts_the_stand_in_back(harness):
    harness.settings["library_surface"] = "esde"
    _platform(harness, 1, "gba")
    _serve_file(harness, 10, "Game (USA).gba")
    path = os.path.join(harness.roms_root, "gba", "Game (USA).gba")
    await _sync(harness)
    harness.romm.download_rom_content_side_effect = RuntimeError("connection reset")

    await _download(harness, 10)

    assert [call.args[0] for call in harness.emit.await_args_list].count("download_failed") == 1
    assert _stands_for(harness, path) == 10
    assert _installed_path(harness, 10) is None


# ── (e) An uninstall and a forget put the stand-in back ────────────────────


async def _downloaded_over_its_stand_in(harness) -> str:
    harness.settings["library_surface"] = "esde"
    _platform(harness, 1, "gba")
    _serve_file(harness, 10, "Game (USA).gba")
    path = os.path.join(harness.roms_root, "gba", "Game (USA).gba")
    await _sync(harness)
    await _download(harness, 10)
    assert _installed_path(harness, 10) == path
    assert _stands_for(harness, path) is None
    return path


async def test_an_uninstall_puts_the_stand_in_back(harness):
    path = await _downloaded_over_its_stand_in(harness)

    result = await harness.endpoints.remove_rom(10)

    assert result["success"] is True
    assert _stands_for(harness, path) == 10


async def test_uninstall_all_puts_the_stand_in_back(harness):
    path = await _downloaded_over_its_stand_in(harness)

    result = await harness.endpoints.uninstall_all_roms()

    assert result["success"] is True
    assert result["removed_count"] == 1
    assert _stands_for(harness, path) == 10


async def test_forgetting_a_download_puts_the_stand_in_back(harness):
    path = await _downloaded_over_its_stand_in(harness)
    os.remove(path)

    result = await harness.endpoints.forget_download(10)

    assert result["success"] is True
    assert _stands_for(harness, path) == 10


# ── (f) Leaving ES-DE removes every stand-in and nothing else ──────────────


async def test_switching_the_surface_to_steam_removes_every_stand_in_and_nothing_else(harness):
    harness.settings["library_surface"] = "esde"
    _platform(harness, 1, "gba")
    _serve_file(harness, 10, "Game (USA).gba")
    _serve_file(harness, 11, "Other (USA).gba")
    await _sync(harness)
    await _download(harness, 11)
    mine = os.path.join(harness.roms_root, "gba", "Mine.gba")
    with open(mine, "wb") as handle:
        handle.write(b"the user's own")
    before = _tree(harness.roms_root)
    stand_in = os.path.join(harness.roms_root, "gba", "Game (USA).gba")
    assert _stands_for(harness, stand_in) == 10

    result = await harness.endpoints.set_library_surface("steam")

    assert result == {"success": True}
    assert _tree(harness.roms_root) == {path: what for path, what in before.items() if path != stand_in}
    with open(os.path.join(harness.data_dir, "esde", "index.json")) as handle:
        assert json.load(handle)["games"] == {}
    assert not os.path.exists(os.path.join(harness.cache_dir, "esde", "stand-ins", "10"))


async def test_switching_to_both_keeps_every_stand_in(harness):
    harness.settings["library_surface"] = "esde"
    _platform(harness, 1, "gba")
    _serve_file(harness, 10, "Game (USA).gba")
    await _sync(harness)
    before = _tree(harness.roms_root)

    await harness.endpoints.set_library_surface("both")

    assert _tree(harness.roms_root) == before


# ── (g) Under the steam surface none of this happens ───────────────────────


async def test_under_steam_a_failed_download_and_an_uninstall_put_no_stand_in_back(harness):
    harness.settings["library_surface"] = "esde"
    _platform(harness, 1, "gba")
    _serve_file(harness, 10, "Game (USA).gba")
    _serve_file(harness, 11, "Other (USA).gba")
    await _sync(harness)
    await _download(harness, 11)
    await harness.endpoints.set_library_surface("steam")
    path = os.path.join(harness.roms_root, "gba", "Game (USA).gba")
    other = os.path.join(harness.roms_root, "gba", "Other (USA).gba")
    harness.romm.download_rom_content_side_effect = RuntimeError("connection reset")

    await _download(harness, 10)
    await harness.endpoints.remove_rom(11)

    assert not os.path.lexists(path)
    assert not os.path.lexists(other)
    assert os.listdir(os.path.join(harness.roms_root, "gba")) == []


async def test_under_steam_a_download_lands_as_it_always_has(harness):
    _serve_file(harness, 10, "Game (USA).gba")
    path = os.path.join(harness.roms_root, "gba", "Game (USA).gba")

    result = await _download(harness, 10)

    assert result["success"] is True
    with open(path, "rb") as handle:
        assert handle.read() == b"GBA-ROM!"
    assert not os.path.exists(os.path.join(harness.data_dir, "esde", "index.json"))
