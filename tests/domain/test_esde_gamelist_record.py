"""Tests for ``domain/esde_gamelist_record.py`` — a game's gamelist entry, and the record of its gamelists."""

from __future__ import annotations

import json

import pytest

from domain.esde_gamelist_record import (
    SystemGamelist,
    decode_gamelist_index,
    encode_gamelist_index,
    gamelist_entry_path,
    gamelist_file,
    is_path_component,
)

_FIELDS = {"name": "Chrono Trigger", "desc": "A time travel RPG."}


class TestAGamesEntryPath:
    def test_a_file_is_its_name_relative_to_the_system_folder(self):
        assert gamelist_entry_path("Chrono Trigger (USA).sfc") == "./Chrono Trigger (USA).sfc"

    def test_a_directory_interpreted_as_a_file_is_the_directory_s_own_name(self):
        assert gamelist_entry_path("Final Fantasy VII.m3u") == "./Final Fantasy VII.m3u"

    @pytest.mark.parametrize("name", ["", ".", "..", "a/b.zip", "a\\b.zip", "x\\"])
    def test_a_name_es_de_would_not_read_back_as_one_entry_has_none(self, name):
        assert gamelist_entry_path(name) is None

    @pytest.mark.parametrize("name", ["bell\x07.zip", "nul\x00.zip", "lone\udcff.zip", "￾.zip"])
    def test_a_name_an_xml_file_cannot_carry_has_none(self, name):
        assert gamelist_entry_path(name) is None

    def test_a_name_with_xml_markup_characters_is_kept_as_it_is(self):
        assert gamelist_entry_path("Tom & Jerry <Rev 1>.gba") == "./Tom & Jerry <Rev 1>.gba"


class TestASystemsGamelistFile:
    def test_it_is_the_system_s_folder_below_the_gamelists_root(self):
        assert gamelist_file("/home/deck/retrodeck/ES-DE/gamelists", "snes") == (
            "/home/deck/retrodeck/ES-DE/gamelists/snes/gamelist.xml"
        )

    @pytest.mark.parametrize("system", ["", ".", "..", "a/b", "nul\x00"])
    def test_a_system_that_is_not_one_path_component_is_refused(self, system):
        with pytest.raises(ValueError, match="path component"):
            gamelist_file("/gamelists", system)


class TestAPathComponent:
    @pytest.mark.parametrize("name", ["snes", "Game (USA).sfc", "a\\b", ".hidden"])
    def test_a_name_with_no_slash_is_one(self, name):
        assert is_path_component(name) is True

    @pytest.mark.parametrize("name", ["", ".", "..", "a/b", "/", "nul\x00"])
    def test_an_empty_dot_slashed_or_nul_name_is_none(self, name):
        assert is_path_component(name) is False


class TestTheRecordOnDisk:
    def test_what_is_written_reads_back_the_same(self):
        index = {
            "snes": SystemGamelist(
                pending={"./Chrono Trigger.sfc": _FIELDS},
                written={"./Earthbound.sfc": {"name": "Earthbound"}, "./Empty.sfc": {}},
            ),
            "gba": SystemGamelist(pending={}, written={}),
        }

        assert decode_gamelist_index(encode_gamelist_index(index)) == index

    def test_an_empty_record_reads_back_empty(self):
        assert decode_gamelist_index(encode_gamelist_index({})) == {}

    def test_it_is_versioned_and_keyed_by_system(self):
        data = json.loads(encode_gamelist_index({"snes": SystemGamelist(pending={"./A.sfc": _FIELDS}, written={})}))

        assert data == {"version": 1, "systems": {"snes": {"pending": {"./A.sfc": _FIELDS}, "written": {}}}}

    @pytest.mark.parametrize(
        "raw",
        [
            "{ not json",
            "[]",
            json.dumps({"version": 2, "systems": {}}),
            json.dumps({"version": 1}),
            json.dumps({"version": 1, "systems": []}),
            json.dumps({"version": 1, "systems": {"snes": []}}),
            json.dumps({"version": 1, "systems": {"snes": {"pending": {}}}}),
            json.dumps({"version": 1, "systems": {"..": {"pending": {}, "written": {}}}}),
            json.dumps({"version": 1, "systems": {"snes": {"pending": {"A.sfc": {}}, "written": {}}}}),
            json.dumps({"version": 1, "systems": {"snes": {"pending": {"./a/b.sfc": {}}, "written": {}}}}),
            json.dumps({"version": 1, "systems": {"snes": {"pending": {"./A.sfc": {"rating": "1"}}, "written": {}}}}),
            json.dumps({"version": 1, "systems": {"snes": {"pending": {"./A.sfc": {"name": 1}}, "written": {}}}}),
            json.dumps({"version": 1, "systems": {"snes": {"pending": {"./A.sfc": []}, "written": {}}}}),
            json.dumps({"version": 1, "systems": {"snes": {"pending": {}, "written": {"A.sfc": {}}}}}),
            json.dumps({"version": 1, "systems": {"snes": {"pending": {}, "written": {"./A.sfc": {"rating": "1"}}}}}),
            json.dumps({"version": 1, "systems": {"snes": {"pending": {}, "written": {"./A.sfc": {"name": None}}}}}),
            json.dumps({"version": 1, "systems": {"snes": {"pending": {}, "written": []}}}),
        ],
    )
    def test_anything_else_is_no_record(self, raw):
        assert decode_gamelist_index(raw) is None
