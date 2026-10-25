"""Contract tests for RomM's media in ES-DE: what a library sync links, what it leaves alone, and what a switch removes.

Driven over the real ``bootstrap()`` + SQLite, the real ES-DE pass, its
stand-in and media services and their adapters, and a RetroDECK seeded under
``tmp_path`` whose home is the real resolver's answer; only the RomM transport
is the fake, which serves each media file by the server path a ROM names it
with. A sync runs as the panel starts one, under the ``esde`` surface, and the
media it queues are waited for before anything is read back.
"""

from __future__ import annotations

import asyncio
import os
from typing import Any

import pytest

from domain.sync_run_kind import SyncRunKind

pytestmark = pytest.mark.usefixtures("seeded_retrodeck")

PNG = b"\x89PNG\r\n\x1a\n" + bytes(56)
JPEG = b"\xff\xd8\xff\xe0" + bytes(60)
WEBP = b"RIFF\x00\x00\x00\x00WEBPVP8 " + bytes(48)
MP4 = b"\x00\x00\x00\x18ftypisom" + bytes(52)
PDF = b"%PDF-1.7\n" + bytes(55)

_OWN = "roms/1/10"
_RES = "/assets/romm/resources/"

# Each image folder, the RomM file filling it, the bytes RomM serves there and
# the extension those bytes are. The cover is stored as ``.jpg`` and is a PNG.
_IMAGES = {
    "covers": (f"{_RES}{_OWN}/cover/big.jpg?ts=2026-01-01 00:00:00", PNG, ".png"),
    "marquees": (f"{_RES}{_OWN}/logo/logo.png", JPEG, ".jpg"),
    "screenshots": (f"{_RES}{_OWN}/screenshots/0.png", WEBP, ".webp"),
    "titlescreens": (f"{_RES}{_OWN}/title_screen/title.png", PNG, ".png"),
    "3dboxes": (f"{_RES}{_OWN}/box3d/box 3d.png", PNG, ".png"),
    "backcovers": (f"{_RES}{_OWN}/box2d_back/back.png", JPEG, ".jpg"),
    "physicalmedia": (f"{_RES}{_OWN}/physical/physical.png", PNG, ".png"),
    "fanart": (f"{_RES}{_OWN}/fanart/fanart.jpg", JPEG, ".jpg"),
    "miximages": (f"{_RES}{_OWN}/miximage/miximage.png", PNG, ".png"),
}
_VIDEO = f"{_RES}{_OWN}/video/video.mp4"
_MANUAL = f"{_RES}{_OWN}/manual/manual.pdf"


def _serve_game(harness) -> dict[str, Any]:
    """A single GBA file on platform 1 with a RomM file for every media folder ES-DE reads."""
    harness.romm.platforms.append({"id": 1, "name": "GBA", "slug": "gba", "rom_count": 1})
    harness.settings["enabled_platforms"] = {"1": True}
    rom = {
        "id": 10,
        "name": "Game",
        "fs_name": "Game (USA).gba",
        "fs_size_bytes": 8,
        "platform_id": 1,
        "platform_name": "GBA",
        "platform_slug": "gba",
        "platform_fs_slug": "gba",
        "has_simple_single_file": True,
        "updated_at": "2026-01-01T00:00:00+00:00",
        "path_cover_large": _IMAGES["covers"][0],
        "merged_screenshots": [_IMAGES["screenshots"][0]],
        "path_video": f"{_OWN}/video/video.mp4",
        "path_manual": f"{_OWN}/manual/manual.pdf",
        "ss_metadata": {
            "logo_path": f"{_OWN}/logo/logo.png",
            "title_screen_path": f"{_OWN}/title_screen/title.png",
            "box3d_path": f"{_OWN}/box3d/box 3d.png",
            "box2d_back_path": f"{_OWN}/box2d_back/back.png",
            "physical_path": f"{_OWN}/physical/physical.png",
            "fanart_path": f"{_OWN}/fanart/fanart.jpg",
            "miximage_path": f"{_OWN}/miximage/miximage.png",
        },
    }
    harness.romm.roms[10] = rom
    harness.romm.download_payloads["rom:10:Game (USA).gba"] = b"GBA-ROM!"
    for path, data, _extension in _IMAGES.values():
        harness.romm.media_files[path] = data
    harness.romm.media_files[_VIDEO] = MP4
    harness.romm.media_files[_MANUAL] = PDF
    return rom


def _media_root(harness) -> str:
    return os.path.join(harness.retrodeck_home, "ES-DE", "downloaded_media")


def _link(harness, folder: str, extension: str, stem: str = "Game (USA)") -> str:
    return os.path.join(_media_root(harness), "gba", folder, stem + extension)


async def _drain() -> None:
    """Await every task the sync, a download or the media service started."""
    for _ in range(20):
        pending = [t for t in asyncio.all_tasks() if t is not asyncio.current_task() and not t.done()]
        if not pending:
            return
        await asyncio.gather(*pending, return_exceptions=True)
        await asyncio.sleep(0)


async def _sync(harness, run_id: str) -> None:
    """One apply run over the fake RomM, as the panel's sync starts it, run to its end with its media."""
    sync = harness.app.services.sync_service
    assert sync._box.try_begin_run(run_id, kind=SyncRunKind.APPLY) is True
    await sync._orchestrator._do_sync_per_unit()
    await _drain()


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


def _read(path: str) -> bytes:
    with open(path, "rb") as handle:
        return handle.read()


async def test_an_esde_sync_links_every_image_type_rom_m_serves_named_by_its_bytes(harness):
    harness.settings["library_surface"] = "esde"
    _serve_game(harness)

    await _sync(harness, "run-1")

    for folder, (_path, data, extension) in _IMAGES.items():
        link = _link(harness, folder, extension)
        assert os.path.islink(link), folder
        assert os.readlink(link).startswith(os.path.realpath(harness.cache_dir) + os.sep)
        assert _read(link) == data
    assert not os.path.lexists(_link(harness, "covers", ".jpg"))
    assert not os.path.lexists(_link(harness, "videos", ".mp4"))
    assert _VIDEO not in harness.romm.media_requests()
    assert os.path.isfile(os.path.join(harness.data_dir, "esde", "media.json"))


async def test_a_cover_of_the_user_s_is_left_alone_and_no_cover_of_tender_s_is_linked_beside_it(harness):
    harness.settings["library_surface"] = "esde"
    _serve_game(harness)
    mine = _link(harness, "covers", ".jpg")
    os.makedirs(os.path.dirname(mine))
    with open(mine, "wb") as handle:
        handle.write(b"the user's own cover")

    await _sync(harness, "run-1")

    assert not os.path.islink(mine)
    assert _read(mine) == b"the user's own cover"
    assert sorted(os.listdir(os.path.dirname(mine))) == ["Game (USA).jpg"]
    assert os.path.islink(_link(harness, "marquees", ".jpg"))


async def test_a_second_sync_with_nothing_changed_makes_no_media_request(harness):
    harness.settings["library_surface"] = "esde"
    _serve_game(harness)
    await _sync(harness, "run-1")
    asked = len(harness.romm.media_requests())
    assert asked == len(_IMAGES)

    await _sync(harness, "run-2")

    assert len(harness.romm.media_requests()) == asked
    assert all(os.path.islink(_link(harness, folder, ext)) for folder, (_p, _d, ext) in _IMAGES.items())


async def test_a_selection_fetches_the_video_and_the_manual_and_links_them(harness):
    harness.settings["library_surface"] = "esde"
    _serve_game(harness)
    await _sync(harness, "run-1")

    harness.app.services.esde_media_service.fetch_on_selection(10)
    await _drain()

    assert _read(_link(harness, "videos", ".mp4")) == MP4
    assert _read(_link(harness, "manuals", ".pdf")) == PDF
    assert "/esde/on-demand/10/" in os.readlink(_link(harness, "videos", ".mp4"))


async def test_a_downloaded_game_keeps_its_media_under_its_download_s_name(harness):
    harness.settings["library_surface"] = "esde"
    _serve_game(harness)
    await _sync(harness, "run-1")

    result = await harness.endpoints.start_download(10, False, None, None, False)
    await _drain()
    await _sync(harness, "run-2")

    assert result == {"success": True, "message": "Download started"}
    assert not os.path.islink(os.path.join(harness.roms_root, "gba", "Game (USA).gba"))
    assert _read(_link(harness, "covers", ".png")) == PNG


async def test_switching_the_surface_to_steam_removes_every_link_of_tender_s_and_nothing_else(harness):
    harness.settings["library_surface"] = "esde"
    _serve_game(harness)
    await _sync(harness, "run-1")
    harness.app.services.esde_media_service.fetch_on_selection(10)
    await _drain()
    mine = _link(harness, "fanart", ".png", stem="Mine")
    with open(mine, "wb") as handle:
        handle.write(b"the user's own fanart")
    scraped = os.path.join(harness.tmp_path, "scraped.png")
    with open(scraped, "wb") as handle:
        handle.write(PNG)
    os.symlink(scraped, _link(harness, "fanart", ".png", stem="Scraped"))
    before = _tree(_media_root(harness))
    tenders = {path for path, what in before.items() if what.startswith("link -> ") and "/esde/" in what}
    assert len(tenders) == len(_IMAGES) + 2

    result = await harness.endpoints.set_library_surface("steam")
    await _drain()

    assert result == {"success": True}
    assert _tree(_media_root(harness)) == {path: what for path, what in before.items() if path not in tenders}
    assert not os.path.exists(os.path.join(harness.cache_dir, "esde", "media"))
    assert not os.path.exists(os.path.join(harness.cache_dir, "esde", "on-demand"))
