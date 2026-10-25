"""``EsdeStandInService`` over fakes: which game gets a stand-in, where, and when one goes.

The file operations are the store's, and the adapter's own tests hold them to
their promise on disk; here the store is in memory, so a test can say exactly
what lies at a path — a stand-in, or something of the user's — and read back
what the service asked of it.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import pytest
from fakes.fake_esde_stand_in_store import FakeEsdeStandInIndex, FakeEsdeStandInStore
from fakes.fake_platform_systems import FakePlatformSystems
from fakes.fake_retrodeck_folders import FakeRetroDeckFolders
from fakes.fake_romm_api import FakeRommApi
from fakes.fake_unit_of_work import FakeUnitOfWork, FakeUnitOfWorkFactory

from domain.esde_stand_in import FILE_SHAPE, FOLDER_SHAPE, StandInRecord, StandInShape
from domain.platform_system import NO_SYSTEM, PlatformSystem
from domain.retrodeck_folders import switched_off
from domain.rom import Rom
from domain.rom_install import RomInstall
from domain.work_unit import WorkUnit
from services.esde import EsdeStandInService, EsdeStandInServiceConfig

if TYPE_CHECKING:
    from domain.library_surface import LibrarySurface

_ROMS = "/roms"


def _platform(slug: str) -> WorkUnit:
    return WorkUnit(type="platform", id=1, name=slug, slug=slug, rom_count=0)


def _collection(member_rom_ids: tuple[int, ...]) -> WorkUnit:
    return WorkUnit(
        type="collection",
        id=5,
        name="Favourites",
        slug="",
        rom_count=len(member_rom_ids),
        collection_kind="standard",
        member_rom_ids=member_rom_ids,
    )


def _file_rom(rom_id: int, slug: str = "gba", *, updated_at: str = "2026-01-01T00:00:00+00:00") -> dict[str, Any]:
    """A listing row of a single plain file, which settles its own stand-in."""
    return {
        "id": rom_id,
        "fs_name": f"Game {rom_id}.gba",
        "platform_slug": slug,
        "has_simple_single_file": True,
        "updated_at": updated_at,
    }


def _disc_rom(rom_id: int, slug: str = "psx", *, updated_at: str = "2026-01-01T00:00:00+00:00") -> dict[str, Any]:
    """A listing row of a multi-file ROM, whose ``files`` the listing does not carry."""
    return {
        "id": rom_id,
        "fs_name": f"Disc {rom_id}",
        "fs_name_no_ext": f"Disc {rom_id}",
        "platform_slug": slug,
        "has_multiple_files": True,
        "updated_at": updated_at,
    }


def _disc_detail(rom: dict[str, Any]) -> dict[str, Any]:
    return {
        **rom,
        "files": [{"file_name": "a.cue", "file_size_bytes": 80}, {"file_name": "a.bin", "file_size_bytes": 900}],
    }


def _gba_path(rom_id: int) -> str:
    return f"{_ROMS}/gba/Game {rom_id}.gba"


def _gba_shape(rom_id: int) -> StandInShape:
    return StandInShape(name=f"Game {rom_id}.gba", kind=FILE_SHAPE)


def _gba_record(rom_id: int, *, slug: str = "gba", updated_at: str | None = "2026-01-01T00:00:00+00:00"):
    return StandInRecord(
        platform_slug=slug, system="gba", updated_at=updated_at, path=_gba_path(rom_id), shape=_gba_shape(rom_id)
    )


@dataclass
class Harness:
    service: EsdeStandInService
    store: FakeEsdeStandInStore
    index: FakeEsdeStandInIndex
    romm_api: FakeRommApi
    uow: FakeUnitOfWork
    folders: FakeRetroDeckFolders
    platform_systems: FakePlatformSystems
    settings: dict[str, Any]
    m3u: dict[str, bool]
    # The games whose download is in flight or queued.
    active: set[int]

    def install(self, rom_id: int, slug: str = "gba") -> None:
        with self.uow:
            self.uow.roms.save(
                Rom.synced(
                    rom_id=rom_id,
                    platform_slug=slug,
                    name=f"Game {rom_id}",
                    fs_name=f"Game {rom_id}.gba",
                    shortcut_app_id=None,
                    synced_at="2026-01-01T00:00:00+00:00",
                )
            )
            self.uow.rom_installs.save(
                RomInstall.mark_installed(
                    rom_id=rom_id,
                    file_path=_gba_path(rom_id),
                    rom_dir=None,
                    platform_slug=slug,
                    system=slug,
                    installed_at="2026-01-01T00:00:00+00:00",
                )
            )

    def detail_reads(self) -> list[int]:
        return [args[0] for name, args, _kwargs in self.romm_api.call_log if name == "get_rom"]


@pytest.fixture
async def h(logger) -> Harness:
    store = FakeEsdeStandInStore()
    index = FakeEsdeStandInIndex()
    romm_api = FakeRommApi()
    uow = FakeUnitOfWork()
    folders = FakeRetroDeckFolders(roms=_ROMS, home="/retrodeck")
    platform_systems = FakePlatformSystems()
    settings: dict[str, Any] = {"library_surface": "esde"}
    m3u: dict[str, bool] = {}
    active: set[int] = set()
    service = EsdeStandInService(
        config=EsdeStandInServiceConfig(
            stand_ins=store,
            index=index,
            retrodeck_folders=folders,
            platform_systems=platform_systems,
            m3u_support=lambda system_name: m3u.get(system_name, True),
            romm_api=romm_api,
            uow_factory=FakeUnitOfWorkFactory(uow),
            settings=settings,
            active_downloads=lambda: set(active),
            loop=asyncio.get_running_loop(),
            logger=logger,
            log_debug=lambda msg: None,
        )
    )
    return Harness(service, store, index, romm_api, uow, folders, platform_systems, settings, m3u, active)


async def _run(
    h: Harness, *units: tuple[WorkUnit, list[dict[str, Any]], set[int] | None], surface: LibrarySurface = "esde"
) -> None:
    """One apply run: each unit placed with the ids given (every ROM's for ``None``), then settled."""
    h.service.begin_run(surface)
    for unit, roms, ids in units:
        await h.service.place(unit, roms, {rom["id"] for rom in roms} if ids is None else ids)
    await h.service.settle()


class TestASurfaceThatWritesNoEsde:
    async def test_a_steam_run_places_and_records_nothing(self, h):
        h.service.begin_run("steam")

        await h.service.place(_platform("gba"), [_file_rom(1)], {1})
        await h.service.settle()

        assert h.store.links == {}
        assert h.index.writes == 0

    async def test_a_steam_run_removes_nothing(self, h):
        h.store.links[_gba_path(1)] = (1, _gba_shape(1))
        h.index.index = {1: _gba_record(1)}

        await _run(h, (_platform("gba"), [], set()), surface="steam")

        assert _gba_path(1) in h.store.links
        assert h.index.index == {1: _gba_record(1)}

    async def test_nothing_is_placed_before_a_run_has_begun(self, h):
        await h.service.place(_platform("gba"), [_file_rom(1)], {1})

        assert h.store.links == {}


class TestPlacingAUnitsRepresentatives:
    @pytest.mark.parametrize("surface", ["esde", "both"])
    async def test_a_representative_gets_its_stand_in_and_a_sibling_none(self, h, surface):
        h.service.begin_run(surface)

        await h.service.place(_platform("gba"), [_file_rom(1), _file_rom(2)], {1})

        assert h.store.links == {_gba_path(1): (1, _gba_shape(1))}
        assert h.index.index == {1: _gba_record(1)}

    async def test_a_single_plain_file_asks_rom_m_nothing(self, h):
        h.service.begin_run("esde")

        await h.service.place(_platform("gba"), [_file_rom(1)], {1})

        assert h.detail_reads() == []

    async def test_an_installed_game_gets_none(self, h):
        h.install(1)
        h.service.begin_run("esde")

        await h.service.place(_platform("gba"), [_file_rom(1)], {1})

        assert h.store.links == {}
        assert 1 not in h.index.index

    async def test_a_path_something_else_holds_is_left_alone(self, h):
        h.store.others.add(_gba_path(1))
        h.service.begin_run("esde")

        await h.service.place(_platform("gba"), [_file_rom(1)], {1})

        assert h.store.links == {}
        assert h.index.index == {1: _gba_record(1)}

    async def test_another_game_s_stand_in_at_the_path_is_left_alone(self, h):
        h.store.links[_gba_path(1)] = (7, _gba_shape(1))
        h.service.begin_run("esde")

        await h.service.place(_platform("gba"), [_file_rom(1)], {1})

        assert h.store.links == {_gba_path(1): (7, _gba_shape(1))}

    async def test_a_shape_that_needs_the_detail_asks_it_once_while_the_rom_is_unchanged(self, h):
        rom = _disc_rom(3)
        h.romm_api.roms[3] = _disc_detail(rom)

        await _run(h, (_platform("psx"), [rom], None))
        await _run(h, (_platform("psx"), [dict(rom)], None))

        assert h.detail_reads() == [3]
        assert h.store.links == {
            f"{_ROMS}/psx/Disc 3.m3u": (3, StandInShape(name="Disc 3.m3u", kind=FOLDER_SHAPE, launch_name="Disc 3.m3u"))
        }

    async def test_a_rom_rom_m_changed_is_asked_about_again(self, h):
        rom = _disc_rom(3)
        h.romm_api.roms[3] = _disc_detail(rom)
        await _run(h, (_platform("psx"), [rom], None))

        await _run(h, (_platform("psx"), [_disc_rom(3, updated_at="2026-03-03T00:00:00+00:00")], None))

        assert h.detail_reads() == [3, 3]
        assert h.index.index[3].updated_at == "2026-03-03T00:00:00+00:00"

    async def test_the_system_s_playlist_support_decides_the_shape(self, h):
        h.m3u["psx"] = False
        rom = _disc_rom(3)
        h.romm_api.roms[3] = _disc_detail(rom)

        await _run(h, (_platform("psx"), [rom], None))

        assert list(h.store.links) == [f"{_ROMS}/psx/a.cue"]

    async def test_a_detail_rom_m_cannot_answer_places_nothing_and_says_so(self, h, caplog):
        h.romm_api.get_rom_side_effect = RuntimeError("RomM is down")
        h.service.begin_run("esde")

        with caplog.at_level(logging.WARNING):
            await h.service.place(_platform("psx"), [_disc_rom(3)], {3})

        assert h.store.links == {}
        assert 3 not in h.index.index
        assert any("RomM is down" in record.getMessage() for record in caplog.records)

    async def test_a_layout_es_de_lists_as_no_one_game_is_recorded_and_not_asked_about_again(self, h):
        rom = _disc_rom(3, slug="ps3")
        h.romm_api.roms[3] = {**rom, "files": [{"file_name": "PS3_GAME/USRDIR/EBOOT.BIN", "file_size_bytes": 9}]}

        await _run(h, (_platform("ps3"), [rom], None))
        await _run(h, (_platform("ps3"), [dict(rom)], None))

        assert h.store.links == {}
        assert h.index.index[3].path is None
        assert h.detail_reads() == [3]

    async def test_a_platform_without_a_system_gets_none(self, h):
        h.platform_systems.answers["gba"] = PlatformSystem(NO_SYSTEM, "gba", "gba")
        h.service.begin_run("esde")

        await h.service.place(_platform("gba"), [_file_rom(1)], {1})

        assert h.store.links == {}

    async def test_a_folder_retrodeck_refuses_gets_none(self, h):
        h.folders.download_refusal = switched_off("Downloads")
        h.service.begin_run("esde")

        await h.service.place(_platform("gba"), [_file_rom(1)], {1})

        assert h.store.links == {}

    async def test_a_link_that_cannot_be_written_is_logged_and_the_rest_placed(self, h, caplog):
        h.store.fail.add(_gba_path(1))
        h.service.begin_run("esde")

        with caplog.at_level(logging.WARNING):
            await h.service.place(_platform("gba"), [_file_rom(1), _file_rom(2)], {1, 2})

        assert list(h.store.links) == [_gba_path(2)]
        assert caplog.records


class TestARunsEnd:
    async def test_a_game_the_run_no_longer_syncs_on_a_platform_it_read_loses_its_stand_in(self, h):
        await _run(h, (_platform("gba"), [_file_rom(1), _file_rom(2)], None))

        await _run(h, (_platform("gba"), [_file_rom(2)], None))

        assert list(h.store.links) == [_gba_path(2)]
        assert set(h.index.index) == {2}
        assert h.store.removed_stubs == [1]

    async def test_a_version_no_longer_its_group_s_representative_loses_its_stand_in(self, h):
        await _run(h, (_platform("gba"), [_file_rom(1), _file_rom(2)], {1}))

        await _run(h, (_platform("gba"), [_file_rom(1), _file_rom(2)], {2}))

        assert list(h.store.links) == [_gba_path(2)]

    async def test_a_platform_no_longer_synced_loses_its_stand_ins(self, h):
        await _run(h, (_platform("gba"), [_file_rom(1)], None))

        await _run(h)

        assert h.store.links == {}
        assert h.index.index == {}

    async def test_a_platform_skipped_as_unchanged_keeps_every_stand_in(self, h):
        await _run(h, (_platform("gba"), [_file_rom(1)], None))
        h.service.begin_run("esde")

        await h.service.place(_platform("gba"), [], set(), skipped=True)
        await h.service.settle()

        assert list(h.store.links) == [_gba_path(1)]

    async def test_a_skipped_collection_keeps_its_members_on_a_platform_not_read(self, h):
        collection = _collection((1,))
        await _run(h, (collection, [_file_rom(1)], None))
        h.service.begin_run("esde")

        await h.service.place(collection, [], set(), skipped=True)
        await h.service.settle()

        assert list(h.store.links) == [_gba_path(1)]

    async def test_a_skipped_collection_keeps_no_member_a_platform_read_named_no_representative(self, h):
        await _run(h, (_platform("gba"), [_file_rom(1)], None))
        h.service.begin_run("esde")

        await h.service.place(_platform("gba"), [_file_rom(1), _file_rom(2)], {2})
        await h.service.place(_collection((1,)), [], set(), skipped=True)
        await h.service.settle()

        assert list(h.store.links) == [_gba_path(2)]

    async def test_an_installed_game_loses_its_stand_in_and_keeps_its_record(self, h):
        await _run(h, (_platform("gba"), [_file_rom(1)], None))
        h.install(1)

        await _run(h, (_platform("gba"), [_file_rom(1)], None))

        assert h.store.links == {}
        assert h.index.index == {1: _gba_record(1)}

    async def test_a_stand_in_nothing_recorded_in_a_folder_the_run_placed_into_goes(self, h):
        h.store.links[f"{_ROMS}/gba/Lost.gba"] = (9, StandInShape(name="Lost.gba", kind=FILE_SHAPE))
        h.store.links[f"{_ROMS}/n64/Lost.z64"] = (8, StandInShape(name="Lost.z64", kind=FILE_SHAPE))

        await _run(h, (_platform("gba"), [_file_rom(1)], None))

        assert set(h.store.links) == {_gba_path(1), f"{_ROMS}/n64/Lost.z64"}

    async def test_a_game_whose_stand_in_moved_loses_the_old_one(self, h):
        h.store.links[f"{_ROMS}/gba/Old name.gba"] = (1, StandInShape(name="Old name.gba", kind=FILE_SHAPE))
        h.index.index = {
            1: StandInRecord(
                platform_slug="gba",
                system="gba",
                updated_at="2025-01-01",
                path=f"{_ROMS}/gba/Old name.gba",
                shape=StandInShape(name="Old name.gba", kind=FILE_SHAPE),
            )
        }

        await _run(h, (_platform("gba"), [_file_rom(1)], None))

        assert list(h.store.links) == [_gba_path(1)]

    async def test_something_of_the_user_s_at_a_recorded_path_is_never_removed(self, h):
        h.index.index = {1: _gba_record(1)}
        h.store.others.add(_gba_path(1))

        await _run(h)

        assert _gba_path(1) in h.store.others
        assert h.index.index == {}

    async def test_a_run_that_ends_without_settling_removes_nothing(self, h):
        await _run(h, (_platform("gba"), [_file_rom(1)], None))
        h.service.begin_run("esde")

        await h.service.place(_platform("gba"), [], set())

        assert list(h.store.links) == [_gba_path(1)]

    async def test_once_settled_the_run_places_and_removes_nothing_more(self, h):
        await _run(h, (_platform("gba"), [_file_rom(1)], None))

        await h.service.place(_platform("gba"), [_file_rom(2)], {2})
        await h.service.settle()

        assert list(h.store.links) == [_gba_path(1)]


class TestOneGame:
    def test_the_game_a_stand_in_or_a_file_directly_inside_a_stand_in_folder_stands_for(self, h):
        h.store.links[f"{_ROMS}/psx/Disc.m3u"] = (
            3,
            StandInShape(name="Disc.m3u", kind=FOLDER_SHAPE, launch_name="Disc.m3u"),
        )
        h.store.links[_gba_path(1)] = (1, _gba_shape(1))

        assert h.service.do_stand_in_at(f"{_ROMS}/psx/Disc.m3u") == 3
        assert h.service.do_stand_in_at(f"{_ROMS}/psx/Disc.m3u/Disc.m3u") == 3
        assert h.service.do_stand_in_at(_gba_path(1)) == 1
        assert h.service.do_stand_in_at(f"{_ROMS}/psx/Other.m3u") is None
        assert h.service.do_stand_in_at(f"{_ROMS}/psx/Disc.m3u/inner/Disc.m3u") is None

    async def test_a_stand_in_and_a_file_inside_a_stand_in_folder_are_stand_ins(self, h):
        h.store.links[f"{_ROMS}/psx/Disc.m3u"] = (
            3,
            StandInShape(name="Disc.m3u", kind=FOLDER_SHAPE, launch_name="Disc.m3u"),
        )

        assert await h.service.is_stand_in(f"{_ROMS}/psx/Disc.m3u")
        assert await h.service.is_stand_in(f"{_ROMS}/psx/Disc.m3u/Disc.m3u")
        assert not await h.service.is_stand_in(f"{_ROMS}/psx/Other.m3u")

    async def test_a_game_s_stand_in_is_its_record(self, h):
        h.index.index = {1: _gba_record(1), 3: StandInRecord("ps3", "ps3", None, None, None)}

        assert await h.service.stand_in_for(1) == _gba_record(1)
        assert await h.service.stand_in_for(3) is None
        assert await h.service.stand_in_for(4) is None

    async def test_clearing_for_a_download_removes_the_link_and_keeps_the_record(self, h):
        await _run(h, (_platform("gba"), [_file_rom(1)], None))

        assert await h.service.clear_for_download(1) is True

        assert h.store.links == {}
        assert h.index.index == {1: _gba_record(1)}
        assert await h.service.clear_for_download(1) is False
        assert await h.service.clear_for_download(4) is False

    async def test_restoring_puts_the_recorded_stand_in_back(self, h):
        await _run(h, (_platform("gba"), [_file_rom(1)], None))
        await h.service.clear_for_download(1)

        assert await h.service.restore(1) is True

        assert h.store.links == {_gba_path(1): (1, _gba_shape(1))}

    async def test_restoring_puts_nothing_back_where_it_may_not(self, h):
        h.index.index = {1: _gba_record(1), 2: _gba_record(2), 3: StandInRecord("ps3", "ps3", None, None, None)}
        h.install(2)
        h.store.others.add(_gba_path(1))

        assert await h.service.restore(1) is False
        assert await h.service.restore(2) is False
        assert await h.service.restore(3) is False
        assert await h.service.restore(4) is False
        assert h.store.links == {}

    async def test_restoring_under_a_surface_that_writes_no_esde_puts_nothing_back(self, h):
        h.index.index = {1: _gba_record(1)}
        h.settings["library_surface"] = "steam"

        assert await h.service.restore(1) is False
        assert h.store.links == {}


class TestRemovingAll:
    async def test_every_stand_in_tender_placed_goes_and_the_record_is_emptied(self, h):
        await _run(h, (_platform("gba"), [_file_rom(1), _file_rom(2)], None))
        h.store.links[f"{_ROMS}/gba/Lost.gba"] = (9, StandInShape(name="Lost.gba", kind=FILE_SHAPE))
        h.store.others.add(f"{_ROMS}/gba/Mine.gba")

        assert await h.service.remove_all() == 3

        assert h.store.links == {}
        assert h.index.index == {}
        assert f"{_ROMS}/gba/Mine.gba" in h.store.others


class TestAGameBeingDownloaded:
    """A game whose download is in flight or queued gets no stand-in until the download ends."""

    async def test_a_run_records_its_stand_in_and_places_none(self, h):
        h.active.add(1)

        await _run(h, (_platform("gba"), [_file_rom(1), _file_rom(2)], None))

        assert list(h.store.links) == [_gba_path(2)]
        assert h.index.index[1] == _gba_record(1)

    async def test_a_download_claimed_while_the_run_decided_still_gets_none(self, h):
        read = h.index.read

        def read_as_a_download_is_claimed():
            h.active.add(1)
            return read()

        h.index.read = read_as_a_download_is_claimed
        h.service.begin_run("esde")

        await h.service.place(_platform("gba"), [_file_rom(1)], {1})

        assert h.store.links == {}

    async def test_a_restore_puts_nothing_back_while_its_download_is_under_way(self, h):
        h.index.index = {1: _gba_record(1)}
        h.active.add(1)

        assert await h.service.restore(1) is False
        assert h.store.links == {}

        h.active.clear()

        assert await h.service.restore(1) is True
        assert list(h.store.links) == [_gba_path(1)]


class TestLeavingEsde:
    async def test_storing_a_surface_that_writes_no_esde_removes_every_stand_in(self, h):
        await _run(h, (_platform("gba"), [_file_rom(1), _file_rom(2)], None))
        h.store.others.add(f"{_ROMS}/gba/Mine.gba")
        h.settings["library_surface"] = "steam"

        await h.service.surface_stored("steam")

        assert h.store.links == {}
        assert h.index.index == {}
        assert h.store.others == {f"{_ROMS}/gba/Mine.gba"}

    @pytest.mark.parametrize("surface", ["esde", "both"])
    async def test_storing_a_surface_that_writes_esde_removes_nothing(self, h, surface):
        await _run(h, (_platform("gba"), [_file_rom(1)], None))

        await h.service.surface_stored(surface)

        assert list(h.store.links) == [_gba_path(1)]
        assert set(h.index.index) == {1}

    async def test_a_run_under_way_places_nothing_once_esde_is_left(self, h):
        h.service.begin_run("esde")
        await h.service.place(_platform("gba"), [_file_rom(1)], {1})
        h.settings["library_surface"] = "steam"

        await h.service.surface_stored("steam")
        await h.service.place(_platform("gba"), [_file_rom(2)], {2})
        await h.service.settle()

        assert h.store.links == {}
        assert h.index.index == {}

    async def test_a_unit_decided_across_the_switch_places_nothing(self, h):
        read, deciding, go = h.index.read, threading.Event(), threading.Event()

        def read_held_once():
            if not deciding.is_set():
                deciding.set()
                go.wait(5)
            return read()

        h.index.read = read_held_once
        h.service.begin_run("esde")
        placing = asyncio.create_task(h.service.place(_platform("gba"), [_file_rom(1)], {1}))
        while not deciding.is_set():
            await asyncio.sleep(0.001)
        h.settings["library_surface"] = "steam"

        await h.service.surface_stored("steam")
        go.set()
        await placing

        assert h.store.links == {}
