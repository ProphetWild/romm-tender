"""``EsdeLibraryPass``: the stand-ins first, then each game's media and gamelist entry under its entry's name.

The stand-in service runs over its in-memory store, and the media and gamelist
services over the real file adapters on ``tmp_path``, all as the composition
root wires them and all handed the pass's calls, so a test reads back where a
game's stand-in stands, where its media are linked and what its system's
gamelist holds.
"""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from typing import Any

import pytest
from fakes.fake_esde_gamelist_store import FakeEsdeGamelistIndex
from fakes.fake_esde_media_index import FakeEsdeMediaIndex
from fakes.fake_esde_running import FakeEsdeRunning
from fakes.fake_esde_stand_in_store import FakeEsdeStandInIndex, FakeEsdeStandInStore
from fakes.fake_platform_systems import FakePlatformSystems
from fakes.fake_retrodeck_folders import FakeRetroDeckFolders
from fakes.fake_romm_api import FakeRommApi
from fakes.fake_unit_of_work import FakeUnitOfWork, FakeUnitOfWorkFactory
from fakes.system_time import FakeClock

from adapters.esde_gamelist_files import EsdeGamelistFilesAdapter
from adapters.esde_media_files import EsdeMediaFilesAdapter
from domain.rom import Rom
from domain.rom_install import RomInstall
from domain.work_unit import WorkUnit
from services.esde import (
    EsdeGamelistService,
    EsdeGamelistServiceConfig,
    EsdeLibraryPass,
    EsdeLibraryPassConfig,
    EsdeMediaService,
    EsdeMediaServiceConfig,
    EsdeStandInService,
    EsdeStandInServiceConfig,
)

_PNG = b"\x89PNG\r\n\x1a\n" + bytes(56)
_UNIT = WorkUnit(type="platform", id=1, name="GBA", slug="gba", rom_count=0)


def _rom(rom_id: int) -> dict[str, Any]:
    return {
        "id": rom_id,
        "name": f"Game {rom_id}",
        "fs_name": f"Game {rom_id}.gba",
        "platform_id": 1,
        "platform_slug": "gba",
        "has_simple_single_file": True,
        "updated_at": "2026-01-01T00:00:00+00:00",
        "path_cover_large": f"/assets/romm/resources/roms/1/{rom_id}/cover/big.png",
    }


async def _idle(media: EsdeMediaService) -> None:
    """Wait until every task *media* started has ended, yielding each round so an ended task is let go."""
    while tasks := list(media._tasks):
        await asyncio.gather(*tasks, return_exceptions=True)
        await asyncio.sleep(0)


@dataclass
class Harness:
    library: EsdeLibraryPass
    stand_ins: FakeEsdeStandInStore
    romm: FakeRommApi
    uow: FakeUnitOfWork
    roms_root: str
    media_root: str
    gamelists_root: str
    esde: FakeEsdeRunning

    def gamelist(self) -> str | None:
        path = os.path.join(self.gamelists_root, "gba", "gamelist.xml")
        if not os.path.exists(path):
            return None
        with open(path, encoding="utf-8") as handle:
            return handle.read()

    def serve(self, *roms: dict[str, Any]) -> None:
        for rom in roms:
            self.romm.media_files[rom["path_cover_large"]] = _PNG

    def cover(self, stem: str) -> str:
        return os.path.join(self.media_root, "gba", "covers", f"{stem}.png")

    def install(self, rom_id: int, file_path: str, rom_dir: str | None = None) -> None:
        with self.uow:
            self.uow.roms.save(
                Rom.synced(
                    rom_id=rom_id,
                    platform_slug="gba",
                    name=f"Game {rom_id}",
                    fs_name=f"Game {rom_id}.gba",
                    shortcut_app_id=None,
                    synced_at="2026-01-01T00:00:00+00:00",
                )
            )
            self.uow.rom_installs.save(
                RomInstall.mark_installed(
                    rom_id=rom_id,
                    file_path=file_path,
                    rom_dir=rom_dir,
                    platform_slug="gba",
                    system="gba",
                    installed_at="2026-01-01T00:00:00+00:00",
                )
            )

    async def run(self, *roms: dict[str, Any], skipped: bool = False, surface: Any = "esde") -> None:
        self.library.begin_run(surface)
        await self.library.place(_UNIT, list(roms), {rom["id"] for rom in roms}, skipped=skipped)
        await self.library.settle()
        await self.idle()

    async def idle(self) -> None:
        await _idle(self.library._media)


@pytest.fixture
async def h(tmp_path, logger) -> Harness:
    loop = asyncio.get_running_loop()
    folders = FakeRetroDeckFolders(roms=str(tmp_path / "roms"), home=str(tmp_path / "retrodeck"))
    romm = FakeRommApi()
    uow = FakeUnitOfWork()
    store = FakeEsdeStandInStore()
    settings: dict[str, Any] = {"library_surface": "esde"}
    stand_ins = EsdeStandInService(
        config=EsdeStandInServiceConfig(
            stand_ins=store,
            index=FakeEsdeStandInIndex(),
            retrodeck_folders=folders,
            platform_systems=FakePlatformSystems(),
            m3u_support=lambda system_name: True,
            romm_api=romm,
            uow_factory=FakeUnitOfWorkFactory(uow),
            settings=settings,
            active_downloads=set,
            loop=loop,
            logger=logger,
            log_debug=lambda msg: None,
        )
    )
    media = EsdeMediaService(
        config=EsdeMediaServiceConfig(
            romm_api=romm,
            files=EsdeMediaFilesAdapter(cache_dir=str(tmp_path / "cache")),
            index=FakeEsdeMediaIndex(),
            retrodeck_folders=folders,
            settings=settings,
            clock=FakeClock(),
            loop=loop,
            logger=logger,
            log_debug=lambda msg: None,
        )
    )
    esde = FakeEsdeRunning()
    gamelists = EsdeGamelistService(
        config=EsdeGamelistServiceConfig(
            files=EsdeGamelistFilesAdapter(),
            index=FakeEsdeGamelistIndex(),
            retrodeck_folders=folders,
            esde_running=esde,
            loop=loop,
            logger=logger,
            log_debug=lambda msg: None,
        )
    )
    library = EsdeLibraryPass(config=EsdeLibraryPassConfig(stand_ins=stand_ins, media=media, gamelists=gamelists))
    esde_root = os.path.join(str(tmp_path / "retrodeck"), "ES-DE")
    return Harness(
        library,
        store,
        romm,
        uow,
        str(tmp_path / "roms"),
        os.path.join(esde_root, "downloaded_media"),
        os.path.join(esde_root, "gamelists"),
        esde,
    )


class TestAUnit:
    async def test_a_representative_gets_its_stand_in_and_its_media_under_the_stand_in_s_name(self, h):
        h.serve(_rom(1))

        await h.run(_rom(1))

        assert os.path.join(h.roms_root, "gba", "Game 1.gba") in h.stand_ins.links
        with open(h.cover("Game 1"), "rb") as handle:
            assert handle.read() == _PNG

    async def test_an_installed_representative_gets_its_media_under_its_download_s_name(self, h):
        h.serve(_rom(1))
        h.install(1, os.path.join(h.roms_root, "gba", "Game 1 (Rev 1).gba"))

        await h.run(_rom(1))

        assert h.stand_ins.links == {}
        assert os.path.islink(h.cover("Game 1 (Rev 1)"))

    async def test_a_unit_skipped_as_unchanged_fetches_nothing(self, h):
        h.serve(_rom(1))

        await h.run(_rom(1), skipped=True)

        assert h.romm.media_requests() == []

    async def test_a_representative_s_fields_reach_its_system_s_gamelist_under_the_stand_in_s_name(self, h):
        h.serve(_rom(1))

        await h.run(_rom(1))

        gamelist = h.gamelist()
        assert gamelist is not None
        assert "<path>./Game 1.gba</path>" in gamelist
        assert "<name>Game 1</name>" in gamelist

    async def test_an_installed_representative_s_entry_is_its_download_s_name(self, h):
        h.serve(_rom(1))
        h.install(1, os.path.join(h.roms_root, "gba", "Game 1 (Rev 1).gba"))

        await h.run(_rom(1))

        assert "<path>./Game 1 (Rev 1).gba</path>" in (h.gamelist() or "")

    async def test_a_unit_skipped_as_unchanged_writes_no_gamelist(self, h):
        await h.run(_rom(1), skipped=True)

        assert h.gamelist() is None

    async def test_a_run_whose_surface_writes_no_esde_fetches_nothing(self, h):
        h.serve(_rom(1))

        await h.run(_rom(1), surface="steam")

        assert h.romm.media_requests() == []
        assert h.stand_ins.links == {}
        assert h.gamelist() is None


class TestARunWhileEsdeRuns:
    async def test_the_gamelist_waits_for_es_de_s_next_start(self, h):
        h.esde.running = True

        await h.run(_rom(1))

        assert h.gamelist() is None
        await h.library._gamelists.write_pending()
        assert "<path>./Game 1.gba</path>" in (h.gamelist() or "")


class TestARunsEnd:
    async def test_a_game_no_longer_synced_loses_its_media_with_its_stand_in(self, h):
        h.serve(_rom(1), _rom(2))
        await h.run(_rom(1), _rom(2))

        await h.run(_rom(1))

        assert os.path.join(h.roms_root, "gba", "Game 2.gba") not in h.stand_ins.links
        assert not os.path.lexists(h.cover("Game 2"))
        assert os.path.islink(h.cover("Game 1"))

    async def test_a_download_that_landed_under_another_name_takes_the_game_s_media_there(self, h):
        h.serve(_rom(1))
        await h.run(_rom(1))
        folder = os.path.join(h.roms_root, "gba", "Game 1.m3u")
        h.install(1, os.path.join(folder, "Game 1.m3u"), rom_dir=folder)
        asked = len(h.romm.media_requests())

        await h.run(skipped=True)

        assert not os.path.lexists(h.cover("Game 1"))
        assert os.path.islink(h.cover("Game 1.m3u"))
        assert len(h.romm.media_requests()) == asked


class TestLeavingEsde:
    async def test_every_stand_in_and_every_media_link_goes(self, h):
        h.serve(_rom(1))
        await h.run(_rom(1))

        await h.library.surface_stored("steam")

        assert h.stand_ins.links == {}
        assert not os.path.lexists(h.cover("Game 1"))

    async def test_what_waits_for_a_gamelist_is_dropped_and_the_gamelist_stays(self, h):
        await h.run(_rom(1))
        written = h.gamelist()
        assert "<path>./Game 1.gba</path>" in (written or "")
        h.esde.running = True
        await h.run(_rom(1), _rom(2))

        await h.library.surface_stored("steam")
        await h.library._gamelists.write_pending()

        assert h.gamelist() == written

    async def test_a_surface_that_writes_esde_keeps_both(self, h):
        h.serve(_rom(1))
        await h.run(_rom(1))

        await h.library.surface_stored("both")

        assert h.stand_ins.links
        assert os.path.islink(h.cover("Game 1"))
