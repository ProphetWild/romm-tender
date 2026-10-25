"""Contract tests for RomM's metadata in ES-DE's gamelists: what a library sync writes, what it keeps, and when.

Driven over the real ``bootstrap()`` + SQLite, the real ES-DE pass with its
stand-in, media and gamelist services and their adapters, and a RetroDECK
seeded under ``tmp_path`` whose home is the real resolver's answer; the RomM
transport is the fake, and so is the question whether ES-DE runs. A sync runs
as the panel starts one, under the ``esde`` surface, and what it starts is
waited for before anything is read back.
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

from ._seed import seed_es_systems

pytestmark = pytest.mark.usefixtures("seeded_retrodeck")

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

# A gamelist as ES-DE leaves one: the user's own game, scraped and made a
# favourite, beside the element ES-DE keeps next to ``<gameList>``.
_MINE = (
    "\t<game>\n"
    "\t\t<path>./Mine (USA).gba</path>\n"
    "\t\t<name>Mine</name>\n"
    "\t\t<desc>Scraped by ES-DE &amp; kept.</desc>\n"
    "\t\t<favorite>true</favorite>\n"
    "\t\t<playcount>3</playcount>\n"
    "\t</game>\n"
)
_USERS_GAMELIST = (
    '<?xml version="1.0"?>\n'
    "<alternativeEmulator>\n\t<label>mGBA</label>\n</alternativeEmulator>\n"
    f"<gameList>\n{_MINE}</gameList>\n"
)


def _serve_game(harness, rom_id: int = 10, *, fs_name: str = "Game (USA).gba", **changes: Any) -> dict[str, Any]:
    """A single GBA file on platform 1 with every field Tender writes into a gamelist."""
    if not harness.romm.platforms:
        harness.romm.platforms.append({"id": 1, "name": "GBA", "slug": "gba", "rom_count": 1})
        harness.settings["enabled_platforms"] = {"1": True}
    rom = {
        "id": rom_id,
        "name": f"Game {rom_id}",
        "summary": f"The story of game {rom_id}.",
        "fs_name": fs_name,
        "fs_size_bytes": 8,
        "platform_id": 1,
        "platform_name": "GBA",
        "platform_slug": "gba",
        "platform_fs_slug": "gba",
        "has_simple_single_file": True,
        "updated_at": "2026-01-01T00:00:00+00:00",
        "metadatum": {
            "genres": ["Platform"],
            "companies": ["Developer Inc", "Publisher Ltd"],
            "first_release_date": 794880000000,
            "player_count": "2",
        },
    }
    rom.update(changes)
    harness.romm.roms[rom_id] = rom
    return rom


def _serve_discs(harness, rom_id: int = 20) -> None:
    """A two-disc PSX game on platform 2 that RomM serves as a folder."""
    harness.romm.platforms.append({"id": 2, "name": "PSX", "slug": "psx", "rom_count": 1})
    harness.settings["enabled_platforms"] = {**harness.settings.get("enabled_platforms", {}), "2": True}
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


def _gamelist_path(harness, system: str = "gba") -> str:
    return os.path.join(harness.retrodeck_home, "ES-DE", "gamelists", system, "gamelist.xml")


def _gamelist(harness, system: str = "gba") -> str | None:
    path = _gamelist_path(harness, system)
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8", newline="") as handle:
        return handle.read()


def _place(harness, text: str, system: str = "gba") -> None:
    path = _gamelist_path(harness, system)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as handle:
        handle.write(text)


def _record(harness) -> dict[str, Any]:
    with open(os.path.join(harness.data_dir, "esde", "gamelists.json"), encoding="utf-8") as handle:
        return json.load(handle)


async def _drain() -> None:
    """Await every task the sync or a download started."""
    for _ in range(20):
        pending = [t for t in asyncio.all_tasks() if t is not asyncio.current_task() and not t.done()]
        if not pending:
            return
        await asyncio.gather(*pending, return_exceptions=True)
        await asyncio.sleep(0)


async def _sync(harness, run_id: str) -> None:
    """One apply run over the fake RomM, as the panel's sync starts it, run to its end."""
    sync = harness.app.services.sync_service
    assert sync._box.try_begin_run(run_id, kind=SyncRunKind.APPLY) is True
    await sync._orchestrator._do_sync_per_unit()
    await _drain()


async def test_an_esde_sync_writes_tender_s_fields_for_each_game_into_its_system_s_gamelist(harness):
    harness.settings["library_surface"] = "esde"
    _serve_game(harness, 10)
    _serve_game(harness, 11, fs_name="Other (Europe).gba")

    await _sync(harness, "run-1")

    gamelist = _gamelist(harness)
    assert gamelist is not None
    assert gamelist.startswith('<?xml version="1.0"?>\n<gameList>\n\t<game>\n\t\t<path>./')
    for path, name in (("./Game (USA).gba", "Game 10"), ("./Other (Europe).gba", "Game 11")):
        start = gamelist.index(f"<path>{path}</path>")
        entry = gamelist[start : gamelist.index("</game>", start)]
        assert f"<name>{name}</name>" in entry
        assert f"<desc>The story of game {name.split()[1]}.</desc>" in entry
        assert "<releasedate>19950311T000000</releasedate>" in entry
        assert "<developer>Developer Inc</developer>" in entry
        assert "<publisher>Publisher Ltd</publisher>" in entry
        assert "<genre>Platform</genre>" in entry
        assert "<players>2</players>" in entry
    assert _record(harness)["systems"]["gba"]["pending"] == {}


async def test_a_game_es_de_lists_as_a_folder_is_entered_under_the_folder_s_name(harness):
    seed_es_systems(harness, _GBA_AND_PSX_XML)
    harness.settings["library_surface"] = "esde"
    _serve_discs(harness)

    await _sync(harness, "run-1")

    assert "<path>./Final Fantasy VII.m3u</path>" in (_gamelist(harness, "psx") or "")


async def test_the_user_s_own_entry_and_a_field_the_user_edited_stay_byte_for_byte(harness):
    harness.settings["library_surface"] = "esde"
    _serve_game(harness, 10)
    _place(harness, _USERS_GAMELIST)
    await _sync(harness, "run-1")
    first = _gamelist(harness)
    assert first is not None
    assert first.startswith(_USERS_GAMELIST.removesuffix("</gameList>\n"))
    assert "<path>./Game (USA).gba</path>" in first
    assert "<desc>The story of game 10.</desc>" in first
    edited = first.replace("<desc>The story of game 10.</desc>", "<desc>My own words.</desc>")
    _place(harness, edited)
    _serve_game(harness, 10, name="Game Ten", summary="RomM's newer words.", updated_at="2026-02-01T00:00:00+00:00")

    await _sync(harness, "run-2")

    assert _gamelist(harness) == edited.replace("<name>Game 10</name>", "<name>Game Ten</name>")


async def test_while_es_de_runs_nothing_is_written_until_its_start_asks(harness):
    harness.settings["library_surface"] = "esde"
    harness.esde_running.running = True
    _serve_game(harness, 10)
    _place(harness, _USERS_GAMELIST)

    await _sync(harness, "run-1")

    assert _gamelist(harness) == _USERS_GAMELIST
    assert list(_record(harness)["systems"]["gba"]["pending"]) == ["./Game (USA).gba"]

    await harness.app.services.esde_gamelist_service.write_pending()

    gamelist = _gamelist(harness)
    assert gamelist is not None
    assert _MINE in gamelist
    assert "<path>./Game (USA).gba</path>" in gamelist
    assert _record(harness)["systems"]["gba"]["pending"] == {}


async def test_switching_the_surface_to_steam_leaves_every_gamelist_as_it_is(harness):
    harness.settings["library_surface"] = "esde"
    _serve_game(harness, 10)
    await _sync(harness, "run-1")
    harness.esde_running.running = True
    _serve_game(harness, 11, fs_name="Other (Europe).gba")
    await _sync(harness, "run-2")
    before = _gamelist(harness)

    result = await harness.endpoints.set_library_surface("steam")
    await harness.app.services.esde_gamelist_service.write_pending()

    assert result == {"success": True}
    assert _gamelist(harness) == before
    assert "./Other (Europe).gba" not in (before or "")
    assert _record(harness)["systems"]["gba"]["pending"] == {}
