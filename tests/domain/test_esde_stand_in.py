"""Tests for ``domain/esde_stand_in.py`` — a stand-in's shape, its target, and the record of what Tender placed.

What a download really leaves on disk is held against these predictions in
``test_esde_stand_in_conformance.py``; the cases here are the rule's own.
"""

from __future__ import annotations

import json
import os

import pytest

from domain.esde_stand_in import (
    FILE_SHAPE,
    FOLDER_SHAPE,
    StandInRecord,
    StandInShape,
    SyncedScope,
    decode_index,
    encode_index,
    listing_settles_shape,
    predict_stand_in,
    stand_in_rom_id,
    stand_in_root,
    wanted_links,
)


def _files(*entries: tuple[str, int]) -> list[dict[str, object]]:
    """RomM's ``files`` for a ROM folder ``/romm/library/psx/Game``, each as ``(path inside it, size)``."""
    out: list[dict[str, object]] = []
    for rel, size in entries:
        directory, name = os.path.split(rel)
        out.append(
            {
                "file_name": name,
                "file_path": "/".join(p for p in ("romm/library/psx/Game", directory) if p),
                "file_size_bytes": size,
            }
        )
    return out


def _multi(*entries: tuple[str, int], **extra: object) -> dict[str, object]:
    return {
        "id": 9,
        "fs_name": "Game",
        "fs_name_no_ext": "Game",
        "full_path": "romm/library/psx/Game",
        "has_multiple_files": True,
        "files": _files(*entries),
        **extra,
    }


def _zip(members: list[tuple[str, int]] | None, **extra: object) -> dict[str, object]:
    entry: dict[str, object] = {"file_name": "Game.zip", "file_size_bytes": 1000}
    if members is not None:
        entry["archive_members"] = [{"name": name, "size": size} for name, size in members]
    return {"id": 9, "fs_name": "Game.zip", "fs_name_no_ext": "Game", "files": [entry], **extra}


def _folder(name: str) -> StandInShape:
    return StandInShape(name=name, kind=FOLDER_SHAPE, launch_name=name)


class TestASingleFile:
    def test_a_plain_file_is_its_own_name(self):
        shape = predict_stand_in({"id": 1, "fs_name": "Game (USA).gba"}, m3u_supported=True)

        assert shape == StandInShape(name="Game (USA).gba", kind=FILE_SHAPE)

    def test_a_nested_single_file_is_named_by_its_file(self):
        rom = {"id": 1, "fs_name": "Game", "has_nested_single_file": True, "files": [{"file_name": "Game.chd"}]}

        assert predict_stand_in(rom, m3u_supported=True) == StandInShape(name="Game.chd", kind=FILE_SHAPE)

    def test_the_name_is_coerced_as_the_download_coerces_it(self):
        assert predict_stand_in({"id": 1, "fs_name": "../../x.gba"}, m3u_supported=True) == StandInShape(
            name="x.gba", kind=FILE_SHAPE
        )
        assert predict_stand_in({"id": 1, "fs_name": ".."}, m3u_supported=True) == StandInShape(
            name="rom_1", kind=FILE_SHAPE
        )


class TestASingleZip:
    def test_one_rom_m_lists_no_members_for_is_the_zip_itself(self):
        assert predict_stand_in(_zip(None), m3u_supported=True) == StandInShape(name="Game.zip", kind=FILE_SHAPE)

    def test_one_whose_members_hold_no_disc_image_is_kept_as_the_zip(self):
        rom = _zip([("Game.gba", 64)])

        assert predict_stand_in(rom, m3u_supported=True) == StandInShape(name="Game.zip", kind=FILE_SHAPE)

    def test_one_holding_a_cue_sheet_is_the_folder_its_playlist_names(self):
        rom = _zip([("Game.cue", 80), ("Game.bin", 5000)])

        assert predict_stand_in(rom, m3u_supported=True) == _folder("Game.m3u")

    def test_without_playlists_it_is_the_folder_the_cue_names(self):
        rom = _zip([("Game (Disc).cue", 80), ("Game (Disc).bin", 5000)])

        assert predict_stand_in(rom, m3u_supported=False) == _folder("Game (Disc).cue")

    def test_a_folder_the_zip_wraps_its_content_in_is_left_out(self):
        rom = _zip([("Game/", 0), ("Game/Game.cue", 80), ("Game/Game.bin", 5000), ("__MACOSX/._Game.cue", 4)])

        assert predict_stand_in(rom, m3u_supported=True) == _folder("Game.m3u")


class TestAMultiFileRom:
    def test_a_disc_set_is_the_folder_its_generated_playlist_names(self):
        rom = _multi(("disc1.cue", 80), ("disc1.bin", 900), ("disc2.cue", 80), ("disc2.bin", 900), fs_name_no_ext="FF7")

        assert predict_stand_in(rom, m3u_supported=True) == _folder("FF7.m3u")

    def test_a_playlist_rom_m_holds_is_the_one_launched(self):
        rom = _multi(("Zelda.m3u", 10), ("Zelda.nsp", 5000))

        assert predict_stand_in(rom, m3u_supported=True) == _folder("Zelda.m3u")

    def test_a_base_file_beside_its_updates_is_the_largest_at_the_top(self):
        rom = _multi(("Zelda.nsp", 9000), ("update/Zelda_update.nsp", 100), ("dlc/Zelda_dlc.nsp", 100))

        assert predict_stand_in(rom, m3u_supported=False) == _folder("Zelda.nsp")

    def test_a_launch_file_in_a_subfolder_gets_no_stand_in(self):
        rom = _multi(("disc/Game.cue", 80), ("disc/Game.bin", 900))

        assert predict_stand_in(rom, m3u_supported=False) is None

    def test_a_folder_boot_layout_gets_no_stand_in(self):
        rom = _multi(("PS3_DISC.SFB", 8), ("PS3_GAME/PARAM.SFO", 16), ("PS3_GAME/USRDIR/EBOOT.BIN", 64))

        assert predict_stand_in(rom, m3u_supported=True) is None

    def test_a_launch_file_the_folder_s_read_order_decides_gets_no_stand_in(self):
        # Two files of one size: the download takes whichever its walk of the
        # folder meets first, which nothing here can know.
        rom = _multi(("a.iso", 500), ("b.iso", 500))

        assert predict_stand_in(rom, m3u_supported=False) is None

    def test_two_cue_sheets_without_a_playlist_get_no_stand_in(self):
        rom = _multi(("disc1.cue", 80), ("disc1.bin", 900), ("disc2.cue", 80), ("disc2.bin", 900))

        assert predict_stand_in(rom, m3u_supported=False) is None

    def test_a_file_that_would_land_outside_the_folder_gets_no_stand_in(self):
        rom = _multi(("Game.cue", 80))
        rom["files"] = [{"file_name": "../evil.cue", "file_size_bytes": 80}]

        assert predict_stand_in(rom, m3u_supported=False) is None

    def test_one_whose_files_are_unknown_gets_no_stand_in(self):
        assert predict_stand_in({"id": 9, "fs_name": "Game", "has_multiple_files": True}, m3u_supported=True) is None


class TestListingSettlesShape:
    def test_a_simple_single_file_is_settled_by_the_listing(self):
        assert listing_settles_shape({"fs_name": "Game.gba", "has_simple_single_file": True})

    @pytest.mark.parametrize(
        "rom",
        [
            {"fs_name": "Game.zip", "has_simple_single_file": True},
            {"fs_name": "Game", "has_nested_single_file": True},
            {"fs_name": "Game", "has_multiple_files": True},
            {"fs_name": "Game.gba"},
            {"fs_name": "Game.gba", "has_simple_single_file": True, "has_multiple_files": True},
        ],
    )
    def test_any_other_shape_needs_rom_m_s_detail(self, rom):
        assert not listing_settles_shape(rom)


class TestTheTarget:
    _ROOT = os.path.join(os.sep, "cache", "romm-tender", "esde", "stand-ins")

    def test_the_root_lies_in_the_cache_root(self):
        assert stand_in_root(os.path.join(os.sep, "cache", "romm-tender")) == self._ROOT

    def test_a_target_below_the_root_names_its_game(self):
        assert stand_in_rom_id(os.path.join(self._ROOT, "42", "Game.gba"), self._ROOT) == 42
        assert stand_in_rom_id(os.path.join(self._ROOT, "42", "Game.m3u", "Game.m3u"), self._ROOT) == 42

    @pytest.mark.parametrize(
        "target",
        [
            os.path.join("esde", "stand-ins", "42", "Game.gba"),
            os.path.join(os.sep, "elsewhere", "42", "Game.gba"),
            os.path.join(os.sep, "cache", "romm-tender", "esde", "stand-ins-other", "42", "Game.gba"),
            os.path.join(os.sep, "cache", "romm-tender", "esde", "stand-ins"),
            os.path.join(os.sep, "cache", "romm-tender", "esde", "stand-ins", "42"),
            os.path.join(os.sep, "cache", "romm-tender", "esde", "stand-ins", "x42", "Game.gba"),
            os.path.join(os.sep, "cache", "romm-tender", "esde", "stand-ins", "²", "Game.gba"),
            os.path.join(os.sep, "cache", "romm-tender", "esde", "stand-ins", "..", "42", "Game.gba"),
        ],
    )
    def test_anything_else_names_none(self, target):
        assert stand_in_rom_id(target, self._ROOT) is None


class TestTheRecord:
    _FILE = StandInRecord(
        platform_slug="gba",
        system="gba",
        updated_at="2026-01-01T00:00:00+00:00",
        path="/roms/gba/Game.gba",
        shape=StandInShape(name="Game.gba", kind=FILE_SHAPE),
    )
    _FOLDER = StandInRecord(
        platform_slug="psx",
        system="psx",
        updated_at=None,
        path="/roms/psx/Game.m3u",
        shape=_folder("Game.m3u"),
    )
    _NONE = StandInRecord(platform_slug="ps3", system="ps3", updated_at="2026-02-02", path=None, shape=None)

    def test_it_reads_back_what_was_written(self):
        index = {1: self._FILE, 2: self._FOLDER, 3: self._NONE}

        assert decode_index(encode_index(index)) == index

    def test_an_empty_record_reads_back_empty(self):
        assert decode_index(encode_index({})) == {}

    @pytest.mark.parametrize(
        "raw",
        [
            "",
            "not json",
            "[]",
            json.dumps({"version": 2, "games": {}}),
            json.dumps({"version": 1}),
            json.dumps({"version": 1, "games": {"x": {}}}),
            json.dumps({"version": 1, "games": {"1": {"platform_slug": "gba"}}}),
            json.dumps(
                {
                    "version": 1,
                    "games": {
                        "1": {"platform_slug": "gba", "system": "gba", "updated_at": None, "path": "/x", "shape": None}
                    },
                }
            ),
            json.dumps(
                {
                    "version": 1,
                    "games": {
                        "1": {
                            "platform_slug": "gba",
                            "system": "gba",
                            "updated_at": None,
                            "path": "/x",
                            "shape": {"name": "x", "kind": "pipe", "launch_name": None},
                        }
                    },
                }
            ),
        ],
    )
    def test_anything_it_cannot_read_reads_as_nothing(self, raw):
        assert decode_index(raw) is None


def _record(platform_slug: str, path: str | None = "/roms/x/Game.gba") -> StandInRecord:
    shape = None if path is None else StandInShape(name=os.path.basename(path), kind=FILE_SHAPE)
    return StandInRecord(platform_slug=platform_slug, system=platform_slug, updated_at=None, path=path, shape=shape)


def _scope(
    *,
    representatives: frozenset[int] = frozenset(),
    read_platforms: frozenset[str] = frozenset(),
    unchanged_platforms: frozenset[str] = frozenset(),
    unchanged_rom_ids: frozenset[int] = frozenset(),
) -> SyncedScope:
    return SyncedScope(
        representatives=representatives,
        read_platforms=read_platforms,
        unchanged_platforms=unchanged_platforms,
        unchanged_rom_ids=unchanged_rom_ids,
    )


class TestWhichStandInsARunKeeps:
    def test_a_representative_is_kept_wherever_it_lies(self):
        scope = _scope(representatives=frozenset({1}), read_platforms=frozenset({"gba"}))

        assert scope.keeps(1, _record("gba"))
        assert scope.keeps(1, _record("switched-off"))

    def test_a_game_the_run_read_its_platform_without_is_not_kept(self):
        # RomM no longer lists it, or a sibling is its group's representative now.
        scope = _scope(representatives=frozenset({2}), read_platforms=frozenset({"gba"}))

        assert not scope.keeps(1, _record("gba"))

    def test_a_game_whose_platform_no_unit_reached_is_not_kept(self):
        assert not _scope(read_platforms=frozenset({"gba"})).keeps(1, _record("switched-off"))

    def test_every_game_on_a_platform_skipped_as_unchanged_is_kept(self):
        assert _scope(unchanged_platforms=frozenset({"gba"})).keeps(1, _record("gba"))

    def test_a_skipped_collection_s_member_is_kept_on_a_platform_the_run_did_not_read(self):
        scope = _scope(unchanged_rom_ids=frozenset({1}), read_platforms=frozenset({"gba"}))

        assert scope.keeps(1, _record("switched-off"))
        assert not scope.keeps(1, _record("gba"))


class TestWantedLinks:
    def test_each_kept_game_s_place_but_an_installed_one_s(self):
        kept = {1: _record("gba", "/roms/gba/A.gba"), 2: _record("gba", "/roms/gba/B.gba"), 3: _record("ps3", None)}

        assert wanted_links(kept, installed={2}) == {"/roms/gba/A.gba": 1}
