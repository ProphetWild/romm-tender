"""``EsdeGamelistService`` over fakes: which entries wait for which system, and when a gamelist is written.

The gamelist files, the record, RetroDECK's folders and the question whether
ES-DE runs are fakes, so a test reads back the text written to each path and
what the record holds; the merge itself is the real one.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

import pytest
from fakes.fake_esde_gamelist_store import FakeEsdeGamelistFiles, FakeEsdeGamelistIndex
from fakes.fake_esde_running import FakeEsdeRunning
from fakes.fake_retrodeck_folders import FakeRetroDeckFolders

from domain.esde_gamelist_record import SystemGamelist
from domain.esde_stand_in import EsdeEntry
from domain.retrodeck_folders import switched_off
from services.esde import EsdeGamelistService, EsdeGamelistServiceConfig

_HOME = "/home/deck/retrodeck"
_GAMELISTS = f"{_HOME}/ES-DE/gamelists"
_SNES = f"{_GAMELISTS}/snes/gamelist.xml"
_GBA = f"{_GAMELISTS}/gba/gamelist.xml"


def _rom(rom_id: int, name: str, **changes: Any) -> dict[str, Any]:
    rom: dict[str, Any] = {"id": rom_id, "name": name, "summary": f"About {name}.", "metadatum": {"player_count": "1"}}
    rom.update(changes)
    return rom


def _entry(rom_id: int, name: str, *, system: str = "snes", is_directory: bool = False) -> EsdeEntry:
    return EsdeEntry(
        rom_id=rom_id,
        platform_slug=system,
        system=system,
        path=f"/roms/{system}/{name}",
        is_directory=is_directory,
    )


@dataclass
class Harness:
    service: EsdeGamelistService
    files: FakeEsdeGamelistFiles
    index: FakeEsdeGamelistIndex
    folders: FakeRetroDeckFolders
    esde: FakeEsdeRunning
    logger: logging.Logger

    def restarted(self) -> EsdeGamelistService:
        """A service as a restart of the backend builds it, over the same files and record."""
        return _service(self.files, self.index, self.folders, self.esde, self.logger)

    def record(self) -> dict[str, SystemGamelist]:
        return self.index.index

    def text(self, path: str) -> str:
        return self.files.files[path]


def _service(files, index, folders, esde, logger) -> EsdeGamelistService:
    return EsdeGamelistService(
        config=EsdeGamelistServiceConfig(
            files=files,
            index=index,
            retrodeck_folders=folders,
            esde_running=esde,
            loop=asyncio.get_running_loop(),
            logger=logger,
            log_debug=lambda msg: None,
        )
    )


@pytest.fixture
async def h(logger) -> Harness:
    files = FakeEsdeGamelistFiles()
    index = FakeEsdeGamelistIndex()
    folders = FakeRetroDeckFolders(roms="/roms", home=_HOME)
    esde = FakeEsdeRunning()
    return Harness(_service(files, index, folders, esde, logger), files, index, folders, esde, logger)


_CHRONO = (_entry(1, "Chrono Trigger.sfc"), _rom(1, "Chrono Trigger"))
_CHRONO_FIELDS = {"name": "Chrono Trigger", "desc": "About Chrono Trigger.", "players": "1"}


async def _collect(h: Harness, *games: tuple[EsdeEntry, dict[str, Any]]) -> None:
    await h.service.collect([entry for entry, _rom in games], {rom["id"]: rom for _entry, rom in games})


class TestAUnitsGames:
    async def test_each_waits_in_its_system_under_the_path_es_de_writes_and_no_gamelist_is_written(self, h):
        await _collect(h, _CHRONO, (_entry(2, "Golden Sun.gba", system="gba"), _rom(2, "Golden Sun")))

        assert h.files.writes == []
        assert h.record() == {
            "snes": SystemGamelist(
                pending={"./Chrono Trigger.sfc": _CHRONO_FIELDS},
                written={},
            ),
            "gba": SystemGamelist(
                pending={"./Golden Sun.gba": {"name": "Golden Sun", "desc": "About Golden Sun.", "players": "1"}},
                written={},
            ),
        }

    async def test_a_directory_interpreted_as_a_file_waits_under_the_directory_s_own_name(self, h):
        await _collect(h, (_entry(3, "Final Fantasy VII.m3u", is_directory=True), _rom(3, "Final Fantasy VII")))

        assert list(h.record()["snes"].pending) == ["./Final Fantasy VII.m3u"]

    async def test_a_game_handed_without_its_rom_adds_nothing(self, h):
        await h.service.collect([_CHRONO[0]], {})

        assert h.record() == {}
        assert h.index.writes == 0

    async def test_a_name_es_de_reads_back_as_no_one_entry_adds_nothing(self, h):
        await _collect(h, (_entry(4, "Back\\slash.sfc"), _rom(4, "Backslash")), _CHRONO)

        assert list(h.record()["snes"].pending) == ["./Chrono Trigger.sfc"]

    async def test_a_game_whose_system_names_no_one_folder_adds_nothing(self, h):
        await _collect(h, (_entry(5, "Game.sfc", system=".."), _rom(5, "Game")), _CHRONO)

        assert list(h.record()) == ["snes"]

    async def test_a_game_handed_again_waits_with_its_newer_fields(self, h):
        await _collect(h, _CHRONO)

        await _collect(h, (_CHRONO[0], _rom(1, "Chrono Trigger DS")))

        assert h.record()["snes"].pending["./Chrono Trigger.sfc"]["name"] == "Chrono Trigger DS"


class TestARunsEnd:
    async def test_with_es_de_not_running_each_system_s_gamelist_is_written_and_nothing_waits(self, h):
        await _collect(h, _CHRONO, (_entry(2, "Golden Sun.gba", system="gba"), _rom(2, "Golden Sun")))

        await h.service.settle()

        assert sorted(h.files.writes) == [_GBA, _SNES]
        assert "<path>./Chrono Trigger.sfc</path>" in h.text(_SNES)
        assert "<desc>About Chrono Trigger.</desc>" in h.text(_SNES)
        assert "<name>Golden Sun</name>" in h.text(_GBA)
        assert h.record()["snes"] == SystemGamelist(
            pending={},
            written={"./Chrono Trigger.sfc": _CHRONO_FIELDS},
        )
        assert h.record()["gba"].pending == {}

    async def test_a_system_with_nothing_waiting_is_not_read_again(self, h, caplog):
        await _collect(h, _CHRONO)
        await h.service.settle()
        h.files.unreadable.add(_SNES)

        await _collect(h, (_entry(2, "Golden Sun.gba", system="gba"), _rom(2, "Golden Sun")))
        with caplog.at_level(logging.WARNING):
            await h.service.settle()

        assert h.files.writes == [_SNES, _GBA]
        assert not any(_SNES in record.getMessage() for record in caplog.records)

    async def test_with_es_de_running_nothing_is_written_and_everything_waits(self, h):
        h.esde.running = True
        await _collect(h, _CHRONO)

        await h.service.settle()

        assert h.files.writes == []
        assert list(h.record()["snes"].pending) == ["./Chrono Trigger.sfc"]

    async def test_an_entry_of_the_user_s_stays_byte_for_byte(self, h):
        mine = "<game>\n\t\t<path>./Mine.sfc</path>\n\t\t<favorite>true</favorite>\n\t</game>"
        h.files.files[_SNES] = f'<?xml version="1.0"?>\n<gameList>\n\t{mine}\n</gameList>\n'
        await _collect(h, _CHRONO)

        await h.service.settle()

        assert mine in h.text(_SNES)
        assert h.text(_SNES).index(mine) < h.text(_SNES).index("./Chrono Trigger.sfc")

    async def test_a_field_the_user_edited_survives_the_next_sync_and_tender_s_own_follow_rom_m(self, h):
        await _collect(h, _CHRONO)
        await h.service.settle()
        h.files.files[_SNES] = h.text(_SNES).replace("About Chrono Trigger.", "My favourite.")

        await _collect(h, (_CHRONO[0], _rom(1, "Chrono Trigger (USA)", summary="Newer words.")))
        await h.service.settle()

        assert "<desc>My favourite.</desc>" in h.text(_SNES)
        assert "<name>Chrono Trigger (USA)</name>" in h.text(_SNES)
        assert h.record()["snes"].written["./Chrono Trigger.sfc"]["desc"] == "About Chrono Trigger."

    async def test_a_gamelist_left_as_it_should_be_is_not_written_again(self, h):
        await _collect(h, _CHRONO)
        await h.service.settle()

        await _collect(h, _CHRONO)
        await h.service.settle()

        assert h.files.writes == [_SNES]
        assert h.record()["snes"].pending == {}

    async def test_a_gamelist_that_is_no_gamelist_is_left_as_it_is_with_a_warning_and_its_entries_wait(self, h, caplog):
        h.files.files[_SNES] = "<gameList><game>"
        await _collect(h, _CHRONO, (_entry(2, "Golden Sun.gba", system="gba"), _rom(2, "Golden Sun")))

        with caplog.at_level(logging.WARNING):
            await h.service.settle()

        assert h.files.writes == [_GBA]
        assert h.text(_SNES) == "<gameList><game>"
        assert list(h.record()["snes"].pending) == ["./Chrono Trigger.sfc"]
        warning = next(record.getMessage() for record in caplog.records if _SNES in record.getMessage())
        assert "not well-formed" in warning

    async def test_a_gamelist_that_cannot_be_read_is_left_alone_and_its_entries_wait(self, h, caplog):
        h.files.unreadable.add(_SNES)
        await _collect(h, _CHRONO)

        with caplog.at_level(logging.WARNING):
            await h.service.settle()

        assert h.files.writes == []
        assert list(h.record()["snes"].pending) == ["./Chrono Trigger.sfc"]
        assert any(_SNES in record.getMessage() for record in caplog.records)

    async def test_a_gamelist_that_cannot_be_written_keeps_its_entries_waiting(self, h, caplog):
        h.files.unwritable.add(_SNES)
        await _collect(h, _CHRONO)

        with caplog.at_level(logging.WARNING):
            await h.service.settle()

        assert list(h.record()["snes"].pending) == ["./Chrono Trigger.sfc"]
        assert h.record()["snes"].written == {}
        assert any(_SNES in record.getMessage() for record in caplog.records)

    async def test_where_es_de_s_folders_are_refused_nothing_is_written_and_everything_waits(self, h):
        await _collect(h, _CHRONO)
        h.folders.download_refusal = switched_off("RetroDECK")

        await h.service.settle()

        assert h.files.writes == []
        assert list(h.record()["snes"].pending) == ["./Chrono Trigger.sfc"]

    async def test_with_nothing_waiting_nothing_is_asked_or_written(self, h):
        await h.service.settle()

        assert h.files.writes == []
        assert h.index.writes == 0


class TestEsdesStart:
    async def test_every_entry_waiting_is_written_whether_or_not_es_de_runs(self, h):
        h.esde.running = True
        await _collect(h, _CHRONO)
        await h.service.settle()

        await h.service.write_pending()

        assert h.files.writes == [_SNES]
        assert "<name>Chrono Trigger</name>" in h.text(_SNES)
        assert h.record()["snes"].pending == {}

    async def test_it_does_not_ask_whether_es_de_runs(self, h):
        await _collect(h, _CHRONO)
        h.esde.calls = 0

        await h.service.write_pending()

        assert h.esde.calls == 0


class TestTheRecord:
    async def test_what_waits_outlives_a_restart_and_is_written_at_es_de_s_next_start(self, h):
        h.esde.running = True
        await _collect(h, _CHRONO)
        await h.service.settle()

        await h.restarted().write_pending()

        assert "<name>Chrono Trigger</name>" in h.text(_SNES)
        assert h.record()["snes"].pending == {}

    async def test_what_was_written_outlives_a_restart_so_a_user_s_edit_still_stands(self, h):
        await _collect(h, _CHRONO)
        await h.service.settle()
        h.files.files[_SNES] = h.text(_SNES).replace("About Chrono Trigger.", "My favourite.")
        restarted = h.restarted()

        await restarted.collect([_CHRONO[0]], {1: _rom(1, "Chrono Trigger", summary="Newer words.")})
        await restarted.settle()

        assert "<desc>My favourite.</desc>" in h.text(_SNES)

    async def test_it_is_read_once(self, h):
        await _collect(h, _CHRONO)
        await h.service.settle()
        await _collect(h, _CHRONO)
        await h.service.write_pending()

        assert h.index.reads == 1

    async def test_a_record_that_cannot_be_written_is_logged_and_the_gamelist_is_written_all_the_same(self, h, caplog):
        h.index.fail_write = True
        await _collect(h, _CHRONO)

        with caplog.at_level(logging.WARNING):
            await h.service.settle()

        assert h.files.writes == [_SNES]
        assert any("record of gamelists" in record.getMessage() for record in caplog.records)


class TestLeavingEsde:
    async def test_what_waits_is_dropped_and_every_gamelist_stays_as_it_is(self, h):
        await _collect(h, _CHRONO)
        await h.service.settle()
        h.esde.running = True
        await _collect(h, (_entry(2, "Earthbound.sfc"), _rom(2, "Earthbound")))
        before = dict(h.files.files)

        await h.service.surface_stored("steam")
        await h.service.write_pending()

        assert h.files.files == before
        assert h.files.writes == [_SNES]
        assert h.record()["snes"].pending == {}
        assert list(h.record()["snes"].written) == ["./Chrono Trigger.sfc"]

    async def test_a_surface_that_writes_es_de_keeps_what_waits(self, h):
        await _collect(h, _CHRONO)

        await h.service.surface_stored("both")

        assert list(h.record()["snes"].pending) == ["./Chrono Trigger.sfc"]
