"""``EsdeMediaService`` over a fake RomM: which media are fetched, kept and linked into ES-DE, and when they go.

The copies and links are the real file adapter's, on ``tmp_path``, so what a
test reads back is what lies in ES-DE's media tree; RomM, RetroDECK's folders,
the record and the clock are fakes. The work is background work, so each test
waits for the service's own tasks to end before it looks.
"""

from __future__ import annotations

import asyncio
import logging
import os
import threading
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import pytest
from fakes.fake_esde_media_index import FakeEsdeMediaIndex
from fakes.fake_retrodeck_folders import FakeRetroDeckFolders
from fakes.fake_romm_api import FakeRommApi
from fakes.system_time import FakeClock

from adapters.esde_media_files import EsdeMediaFilesAdapter
from domain.esde_media import SYNC_FOLDERS, media_sources
from domain.esde_stand_in import EsdeEntry, SyncedScope
from domain.retrodeck_folders import switched_off
from services.esde import EsdeMediaService, EsdeMediaServiceConfig

if TYPE_CHECKING:
    from models.cover import CoverRevalidation

PNG = b"\x89PNG\r\n\x1a\n" + bytes(56)
JPEG = b"\xff\xd8\xff\xe0" + bytes(60)
WEBP = b"RIFF\x00\x00\x00\x00WEBPVP8 " + bytes(48)
MP4 = b"\x00\x00\x00\x18ftypisom" + bytes(52)
PDF = b"%PDF-1.7\n" + bytes(55)
GIF = b"GIF89a" + bytes(58)

_RES = "/assets/romm/resources/roms/1"
_UPDATED = "2026-01-01T00:00:00+00:00"

# What RomM serves for each folder: a cover stored as ``.jpg`` that is a PNG,
# so the name ES-DE finds is what the bytes are.
_BYTES = {
    "covers": PNG,
    "marquees": JPEG,
    "screenshots": WEBP,
    "titlescreens": PNG,
    "3dboxes": PNG,
    "backcovers": JPEG,
    "physicalmedia": PNG,
    "fanart": JPEG,
    "miximages": PNG,
    "videos": MP4,
    "manuals": PDF,
}
_EXTENSIONS = {PNG: ".png", JPEG: ".jpg", WEBP: ".webp", MP4: ".mp4", PDF: ".pdf"}


def _rom(rom_id: int = 10, *, updated_at: str = _UPDATED, **changes: Any) -> dict[str, Any]:
    """A RomM ROM as the listing carries it, with a file for every folder Tender fills."""
    own = f"roms/1/{rom_id}"
    rom: dict[str, Any] = {
        "id": rom_id,
        "platform_id": 1,
        "updated_at": updated_at,
        "path_cover_large": f"{_RES}/{rom_id}/cover/big.jpg?ts=2026-01-01 00:00:00",
        "merged_screenshots": [f"{_RES}/{rom_id}/screenshots/0.jpg"],
        "path_video": f"{own}/video/video.mp4",
        "path_manual": f"{own}/manual/manual.pdf",
        "ss_metadata": {
            "logo_path": f"{own}/logo/logo.png",
            "title_screen_path": f"{own}/title_screen/title.png",
            "box3d_path": f"{own}/box3d/box3d.png",
            "box2d_back_path": f"{own}/box2d_back/back.png",
            "physical_path": f"{own}/physical/physical.png",
            "fanart_path": f"{own}/fanart/fanart.png",
            "miximage_path": f"{own}/miximage/miximage.png",
        },
    }
    rom.update(changes)
    return rom


def _entry(rom_id: int = 10, name: str = "Game (USA).gba", *, is_directory: bool = False) -> EsdeEntry:
    return EsdeEntry(
        rom_id=rom_id, platform_slug="gba", system="gba", path=f"/roms/gba/{name}", is_directory=is_directory
    )


def _scope(*representatives: int) -> SyncedScope:
    return SyncedScope(
        representatives=frozenset(representatives),
        read_platforms=frozenset({"gba"}),
        unchanged_platforms=frozenset(),
        unchanged_rom_ids=frozenset(),
    )


class _HeldRomm(FakeRommApi):
    """A RomM whose media fetches wait at a gate, counting how many wait at once."""

    def __init__(self) -> None:
        super().__init__()
        self.gate = threading.Event()
        self._count = threading.Lock()
        self.waiting = 0
        self.most_at_once = 0

    def download_media(
        self, server_path: str, dest: str, *, etag: str | None = None, last_modified: str | None = None
    ) -> CoverRevalidation | None:
        with self._count:
            self.waiting += 1
            self.most_at_once = max(self.most_at_once, self.waiting)
        self.gate.wait(10)
        with self._count:
            self.waiting -= 1
        return super().download_media(server_path, dest, etag=etag, last_modified=last_modified)


@dataclass
class Harness:
    service: EsdeMediaService
    romm: FakeRommApi
    files: EsdeMediaFilesAdapter
    index: FakeEsdeMediaIndex
    folders: FakeRetroDeckFolders
    settings: dict[str, Any]
    clock: FakeClock
    media_root: str
    cache: str

    def serve(self, rom: dict[str, Any], **only: bytes) -> None:
        """Serve every file *rom* names, each as ``_BYTES`` says, or only the folders *only* names, as given."""
        for folder, path in media_sources(rom).items():
            if not only or folder in only:
                self.romm.media_files[path] = only.get(folder, _BYTES[folder])

    def link(self, folder: str, stem: str = "Game (USA)", extension: str | None = None) -> str:
        extension = extension or _EXTENSIONS[_BYTES[folder]]
        return os.path.join(self.media_root, "gba", folder, stem + extension)

    async def sync(self, *entries: tuple[EsdeEntry, dict[str, Any] | None]) -> None:
        await self.service.queue(
            [entry for entry, _rom in entries], {entry.rom_id: rom for entry, rom in entries if rom is not None}
        )
        await self.idle()

    async def idle(self) -> None:
        """Wait until every task the service started has ended, yielding each round so an ended task is let go."""
        while tasks := list(self.service._tasks):
            await asyncio.gather(*tasks, return_exceptions=True)
            await asyncio.sleep(0)


def _build(tmp_path, logger, romm: FakeRommApi, *, cap: int = 8 * 1024**3) -> Harness:
    files = EsdeMediaFilesAdapter(cache_dir=str(tmp_path / "cache"))
    index = FakeEsdeMediaIndex()
    folders = FakeRetroDeckFolders(roms=str(tmp_path / "roms"), home=str(tmp_path / "retrodeck"))
    settings: dict[str, Any] = {"library_surface": "esde"}
    clock = FakeClock()
    service = EsdeMediaService(
        config=EsdeMediaServiceConfig(
            romm_api=romm,
            files=files,
            index=index,
            retrodeck_folders=folders,
            settings=settings,
            clock=clock,
            loop=asyncio.get_running_loop(),
            logger=logger,
            log_debug=lambda msg: None,
            on_demand_cap_bytes=cap,
        )
    )
    media_root = os.path.join(str(tmp_path / "retrodeck"), "ES-DE", "downloaded_media")
    return Harness(service, romm, files, index, folders, settings, clock, media_root, str(tmp_path / "cache"))


@pytest.fixture
async def h(tmp_path, logger) -> Harness:
    return _build(tmp_path, logger, FakeRommApi())


def _read(path: str) -> bytes:
    with open(path, "rb") as handle:
        return handle.read()


def _write(path: str, data: bytes = b"the user's own") -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as handle:
        handle.write(data)


class TestAtSyncTime:
    async def test_every_image_type_rom_m_serves_is_linked_under_the_entry_s_stem_named_by_its_bytes(self, h):
        rom = _rom()
        h.serve(rom)

        await h.sync((_entry(), rom))

        for folder in SYNC_FOLDERS:
            assert _read(h.link(folder)) == _BYTES[folder], folder
            assert os.readlink(h.link(folder)).startswith(os.path.realpath(h.cache) + os.sep)
        assert not os.path.lexists(h.link("covers", extension=".jpg"))

    async def test_videos_and_manuals_are_recorded_and_not_fetched(self, h):
        rom = _rom()
        h.serve(rom)

        await h.sync((_entry(), rom))

        assert not any("/video/" in path or "/manual/" in path for path in h.romm.media_requests())
        folders = h.index.index[10].folders
        assert folders["videos"].server_path == "/assets/romm/resources/roms/1/10/video/video.mp4"
        assert folders["manuals"].extension is None
        assert not os.path.lexists(h.link("videos"))

    async def test_queueing_answers_before_anything_is_fetched(self, h):
        rom = _rom()
        h.serve(rom)

        await h.service.queue([_entry()], {10: rom})

        assert h.romm.media_requests() == []
        await h.idle()
        assert len(h.romm.media_requests()) == len(SYNC_FOLDERS)

    async def test_an_unchanged_library_costs_no_media_request(self, h):
        rom = _rom()
        h.serve(rom)
        await h.sync((_entry(), rom))
        asked = len(h.romm.media_requests())

        await h.sync((_entry(), _rom()))

        assert len(h.romm.media_requests()) == asked

    async def test_a_changed_updated_at_asks_again_revalidating_what_is_kept(self, h):
        rom = _rom()
        h.serve(rom)
        cover = media_sources(rom)["covers"]
        h.romm.media_etags["/assets/romm/resources/roms/1/10/cover/big.jpg"] = '"v1"'
        await h.sync((_entry(), rom))
        asked = len(h.romm.media_requests())

        await h.sync((_entry(), _rom(updated_at="2026-02-01T00:00:00+00:00")))

        again = h.romm.call_log[-len(SYNC_FOLDERS) :]
        assert len(h.romm.media_requests()) == asked + len(SYNC_FOLDERS)
        assert [kwargs["etag"] for _name, args, kwargs in again if args[0] == cover] == ['"v1"']
        assert _read(h.link("covers")) == PNG

    async def test_a_path_that_moved_fetches_the_new_file_whole(self, h):
        rom = _rom()
        h.serve(rom)
        await h.sync((_entry(), rom))
        moved = _rom(path_cover_large=f"{_RES}/10/cover/other.png")
        h.romm.media_files[f"{_RES}/10/cover/other.png"] = JPEG

        await h.sync((_entry(), moved))

        assert _read(h.link("covers", extension=".jpg")) == JPEG
        assert not os.path.lexists(h.link("covers", extension=".png"))

    async def test_a_file_rom_m_does_not_serve_leaves_its_slot_empty_and_the_rest_done(self, h):
        rom = _rom()
        h.serve(rom)
        del h.romm.media_files[media_sources(rom)["3dboxes"]]

        await h.sync((_entry(), rom))
        await h.sync((_entry(), _rom()))

        assert not os.path.lexists(h.link("3dboxes"))
        assert all(os.path.islink(h.link(folder)) for folder in SYNC_FOLDERS if folder != "3dboxes")
        assert h.romm.media_requests().count(media_sources(rom)["3dboxes"]) == 1

    async def test_bytes_es_de_cannot_read_are_neither_kept_nor_linked(self, h):
        rom = _rom()
        h.serve(rom, covers=GIF)

        await h.sync((_entry(), rom))

        assert not any(os.path.lexists(h.link("covers", extension=e)) for e in (".png", ".jpg", ".webp"))
        assert h.index.index[10].folders["covers"].extension is None
        assert h.files.kept_size(10, "covers", ".png", on_demand=False) is None

    async def test_a_fetch_that_fails_leaves_the_slot_as_it_was_and_is_asked_again(self, h, caplog):
        rom = _rom()
        h.serve(rom)
        cover = media_sources(rom)["covers"]
        h.romm.download_media_side_effects[cover] = ConnectionResetError("reset")

        with caplog.at_level(logging.WARNING):
            await h.sync((_entry(), rom))
        h.romm.download_media_side_effects.clear()
        await h.sync((_entry(), _rom()))

        assert any("reset" in record.getMessage() for record in caplog.records)
        assert h.romm.media_requests().count(cover) == 2
        assert _read(h.link("covers")) == PNG

    async def test_media_rom_m_no_longer_has_are_unlinked_and_forgotten(self, h):
        rom = _rom()
        h.serve(rom)
        await h.sync((_entry(), rom))
        without = _rom(ss_metadata={**rom["ss_metadata"], "logo_path": None})

        await h.sync((_entry(), without))

        assert not os.path.lexists(h.link("marquees"))
        assert h.files.kept_size(10, "marquees", ".jpg", on_demand=False) is None
        assert "marquees" not in h.index.index[10].folders

    async def test_a_cover_of_the_user_s_is_left_alone_and_no_cover_is_linked_beside_it(self, h):
        rom = _rom()
        h.serve(rom)
        mine = h.link("covers", extension=".jpg")
        _write(mine)

        await h.sync((_entry(), rom))

        assert _read(mine) == b"the user's own"
        assert not os.path.lexists(h.link("covers", extension=".png"))
        assert os.path.islink(h.link("marquees"))

    async def test_a_new_stem_takes_the_links_with_it_and_fetches_nothing(self, h):
        rom = _rom()
        h.serve(rom)
        await h.sync((_entry(), rom))
        asked = len(h.romm.media_requests())

        await h.sync((_entry(name="Game (USA).m3u", is_directory=True), None))

        assert len(h.romm.media_requests()) == asked
        assert not os.path.lexists(h.link("covers"))
        assert _read(h.link("covers", stem="Game (USA).m3u")) == PNG
        assert h.index.index[10].stem == "Game (USA).m3u"

    async def test_an_entry_unchanged_without_its_rom_touches_nothing(self, h):
        rom = _rom()
        h.serve(rom)
        await h.sync((_entry(), rom))
        writes = h.index.writes

        await h.sync((_entry(), None))

        assert h.index.writes == writes

    async def test_a_move_queued_over_a_fetch_keeps_the_fetch(self, h):
        rom = _rom()
        h.serve(rom)

        await h.service.queue([_entry()], {10: rom})
        await h.service.queue([_entry(name="Game (USA).m3u", is_directory=True)], {})
        await h.idle()

        assert _read(h.link("covers", stem="Game (USA).m3u")) == PNG

    async def test_a_media_tree_retrodeck_refuses_queues_nothing(self, h):
        rom = _rom()
        h.serve(rom)
        h.folders.download_refusal = switched_off("Games in ES-DE")

        await h.sync((_entry(), rom))

        assert h.romm.media_requests() == []

    async def test_the_record_is_read_once_and_written_once_the_queue_runs_dry(self, h):
        roms = [_rom(rom_id) for rom_id in (10, 11, 12)]
        for rom in roms:
            h.serve(rom)

        await h.sync(*((_entry(rom["id"], f"Game {rom['id']}.gba"), rom) for rom in roms))

        assert (h.index.reads, h.index.writes) == (1, 1)
        assert set(h.index.index) == {10, 11, 12}


class TestHowManyAtOnce:
    async def test_no_more_than_three_fetches_run_at_once(self, tmp_path, logger):
        romm = _HeldRomm()
        h = _build(tmp_path, logger, romm)
        roms = [_rom(rom_id) for rom_id in range(10, 15)]
        for rom in roms:
            h.serve(rom, covers=PNG)
        await h.service.queue([_entry(rom["id"], f"Game {rom['id']}.gba") for rom in roms], {r["id"]: r for r in roms})

        for _ in range(500):
            if romm.waiting == 3:
                break
            await asyncio.sleep(0.01)
        romm.gate.set()
        await h.idle()

        assert romm.most_at_once == 3
        assert all(os.path.islink(h.link("covers", stem=f"Game {rom['id']}")) for rom in roms)

    async def test_a_game_queued_again_before_it_ran_runs_once_as_the_newer_queueing_says(self, tmp_path, logger):
        romm = _HeldRomm()
        h = _build(tmp_path, logger, romm)
        busy = [_rom(rom_id) for rom_id in (1, 2, 3)]
        for rom in busy:
            h.serve(rom, covers=PNG)
        await h.service.queue([_entry(r["id"], f"Game {r['id']}.gba") for r in busy], {r["id"]: r for r in busy})
        for _ in range(500):
            if romm.waiting == 3:
                break
            await asyncio.sleep(0.01)
        later = _rom(4)
        h.serve(later, covers=PNG)

        await h.service.queue([_entry(4, "Game 4.gba")], {4: later})
        await h.service.queue([_entry(4, "Game 4.gba")], {4: _rom(4, updated_at="2026-02-01T00:00:00+00:00")})
        romm.gate.set()
        await h.idle()

        assert romm.media_requests().count(media_sources(later)["covers"]) == 1
        assert h.index.index[4].updated_at == "2026-02-01T00:00:00+00:00"

    async def test_a_game_removed_while_its_fetch_is_under_way_links_nothing(self, tmp_path, logger):
        romm = _HeldRomm()
        h = _build(tmp_path, logger, romm)
        rom = _rom()
        h.serve(rom, covers=PNG)
        await h.service.queue([_entry()], {10: rom})
        for _ in range(500):
            if romm.waiting:
                break
            await asyncio.sleep(0.01)

        await h.service.settle(_scope())
        romm.gate.set()
        await h.idle()

        assert not os.path.lexists(h.link("covers"))
        assert 10 not in h.index.index


class TestARunsEnd:
    async def test_a_game_the_run_no_longer_syncs_loses_its_links_and_copies(self, h):
        for rom_id in (10, 11):
            h.serve(_rom(rom_id))
        await h.sync((_entry(10, "Game 10.gba"), _rom(10)), (_entry(11, "Game 11.gba"), _rom(11)))

        await h.service.settle(_scope(11))

        assert not os.path.lexists(h.link("covers", stem="Game 10"))
        assert os.path.islink(h.link("covers", stem="Game 11"))
        assert not os.path.exists(os.path.join(h.cache, "esde", "media", "10"))
        assert set(h.index.index) == {11}

    async def test_a_game_kept_keeps_everything(self, h):
        rom = _rom()
        h.serve(rom)
        await h.sync((_entry(), rom))

        await h.service.settle(_scope(10))

        assert all(os.path.islink(h.link(folder)) for folder in SYNC_FOLDERS)


class TestOnSelection:
    async def _synced(self, h: Harness, rom_id: int = 10) -> None:
        rom = _rom(rom_id)
        h.serve(rom)
        await h.sync((_entry(rom_id, f"Game {rom_id}.gba"), rom))

    async def test_the_video_and_the_manual_are_fetched_into_the_capped_cache_and_linked(self, h):
        await self._synced(h)

        h.service.fetch_on_selection(10)
        await h.idle()

        assert _read(h.link("videos", stem="Game 10")) == MP4
        assert _read(h.link("manuals", stem="Game 10")) == PDF
        assert "/on-demand/10/" in os.readlink(h.link("videos", stem="Game 10"))
        video = h.index.index[10].folders["videos"]
        assert (video.last_selected, video.size_bytes) == (h.clock.time(), len(MP4))

    async def test_it_answers_at_once(self, h):
        await self._synced(h)
        asked = len(h.romm.media_requests())

        assert h.service.fetch_on_selection(10) is None
        assert len(h.romm.media_requests()) == asked
        await h.idle()

    async def test_a_selection_while_one_is_under_way_is_the_same_one(self, h):
        await self._synced(h)
        asked = len(h.romm.media_requests())

        h.service.fetch_on_selection(10)
        h.service.fetch_on_selection(10)
        await h.idle()

        assert len(h.romm.media_requests()) == asked + 2

    async def test_selecting_it_again_fetches_nothing_and_stamps_it(self, h):
        await self._synced(h)
        h.service.fetch_on_selection(10)
        await h.idle()
        asked = len(h.romm.media_requests())
        h.clock.advance(60)

        h.service.fetch_on_selection(10)
        await h.idle()

        assert len(h.romm.media_requests()) == asked
        assert h.index.index[10].folders["videos"].last_selected == h.clock.time()

    async def test_the_cap_evicts_the_least_recently_selected_link_first(self, tmp_path, logger):
        h = _build(tmp_path, logger, FakeRommApi(), cap=len(MP4) + len(PDF))
        for rom_id in (10, 11):
            await self._synced(h, rom_id)

        h.service.fetch_on_selection(10)
        await h.idle()
        h.clock.advance(60)
        h.service.fetch_on_selection(11)
        await h.idle()

        assert not os.path.lexists(h.link("videos", stem="Game 10"))
        assert not os.path.lexists(h.link("manuals", stem="Game 10"))
        assert h.files.kept_size(10, "videos", ".mp4", on_demand=True) is None
        assert os.path.islink(h.link("videos", stem="Game 11"))
        assert h.index.index[10].folders["videos"].server_path.endswith("video.mp4")

    async def test_nothing_is_fetched_while_the_surface_writes_no_esde(self, h):
        await self._synced(h)
        h.settings["library_surface"] = "steam"
        asked = len(h.romm.media_requests())

        h.service.fetch_on_selection(10)
        await h.idle()

        assert len(h.romm.media_requests()) == asked

    async def test_a_game_no_sync_recorded_fetches_nothing(self, h):
        h.service.fetch_on_selection(99)
        await h.idle()

        assert h.romm.media_requests() == []


class TestLeavingEsde:
    async def test_every_link_of_tender_s_goes_and_nothing_else(self, h, tmp_path):
        rom = _rom()
        h.serve(rom)
        await h.sync((_entry(), rom))
        h.service.fetch_on_selection(10)
        await h.idle()
        mine = h.link("covers", stem="Mine", extension=".jpg")
        _write(mine)
        elsewhere = str(tmp_path / "scraped.png")
        _write(elsewhere, PNG)
        os.symlink(elsewhere, h.link("fanart", stem="Scraped", extension=".png"))

        await h.service.surface_stored("steam")

        remaining = [
            os.path.join(directory, name)
            for directory, folders, names in os.walk(h.media_root)
            for name in folders + names
            if not os.path.isdir(os.path.join(directory, name)) or os.path.islink(os.path.join(directory, name))
        ]
        assert sorted(remaining) == sorted([mine, h.link("fanart", stem="Scraped", extension=".png")])
        assert h.index.index == {}
        assert not os.path.exists(os.path.join(h.cache, "esde", "media"))

    @pytest.mark.parametrize("surface", ["esde", "both"])
    async def test_a_surface_that_writes_esde_removes_nothing(self, h, surface):
        rom = _rom()
        h.serve(rom)
        await h.sync((_entry(), rom))

        await h.service.surface_stored(surface)

        assert all(os.path.islink(h.link(folder)) for folder in SYNC_FOLDERS)

    async def test_a_fetch_under_way_links_nothing_once_es_de_is_left(self, tmp_path, logger):
        romm = _HeldRomm()
        h = _build(tmp_path, logger, romm)
        rom = _rom()
        h.serve(rom, covers=PNG)
        await h.service.queue([_entry()], {10: rom})
        for _ in range(500):
            if romm.waiting:
                break
            await asyncio.sleep(0.01)

        await h.service.surface_stored("steam")
        romm.gate.set()
        await h.idle()

        assert not os.path.lexists(h.link("covers"))
        assert h.index.index == {}


class TestShuttingDown:
    async def test_what_the_record_holds_and_was_not_written_is_written(self, h):
        rom = _rom()
        h.serve(rom)
        h.index.fail_write = True
        await h.sync((_entry(), rom))
        assert h.index.writes == 0
        h.index.fail_write = False

        await h.service.shutdown()

        assert h.index.writes == 1
        assert set(h.index.index) == {10}

    async def test_the_work_under_way_is_stopped(self, tmp_path, logger):
        romm = _HeldRomm()
        h = _build(tmp_path, logger, romm)
        rom = _rom()
        h.serve(rom, covers=PNG)
        await h.service.queue([_entry()], {10: rom})
        for _ in range(500):
            if romm.waiting:
                break
            await asyncio.sleep(0.01)

        await h.service.shutdown()
        romm.gate.set()

        assert list(h.service._tasks) == []
        assert not os.path.lexists(h.link("covers"))
