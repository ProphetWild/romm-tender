"""``EsdeStandInsAdapter`` on ``tmp_path``: stubs in a cache root, links in a system's ROM folder.

The adapter's one promise beyond placing a link is that it never replaces or
removes anything that is not a stand-in: every such case is set up for real
here — a user's file, a folder, a link of somebody else's, another game's
stand-in — and checked untouched afterwards.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from adapters.esde_stand_ins import EsdeStandInsAdapter
from domain.esde_stand_in import FILE_SHAPE, FOLDER_SHAPE, KEPT, OCCUPIED, PLACED, StandInShape
from lib.path_safety import PathTraversalError

_FILE = StandInShape(name="Game (USA).gba", kind=FILE_SHAPE)
_FOLDER = StandInShape(name="Game.m3u", kind=FOLDER_SHAPE, launch_name="Game.m3u")


@pytest.fixture
def cache(tmp_path) -> Path:
    return tmp_path / "cache"


@pytest.fixture
def folder(tmp_path) -> Path:
    path = tmp_path / "roms" / "gba"
    path.mkdir(parents=True)
    return path


@pytest.fixture
def adapter(cache) -> EsdeStandInsAdapter:
    return EsdeStandInsAdapter(cache_dir=str(cache))


def _game(cache: Path, rom_id: int) -> Path:
    return Path(os.path.realpath(cache)) / "esde" / "stand-ins" / str(rom_id)


def _stub(cache: Path, rom_id: int, name: str) -> Path:
    return _game(cache, rom_id) / name


class TestPlacing:
    def test_a_file_s_stand_in_is_a_link_to_an_empty_stub_in_the_cache_root(self, adapter, cache, folder):
        path = folder / _FILE.name

        assert adapter.place(42, _FILE, str(path)) == PLACED

        assert path.is_symlink()
        assert os.readlink(path) == str(_stub(cache, 42, _FILE.name))
        assert os.path.isabs(os.readlink(path))
        assert path.is_file()
        assert path.stat().st_size == 0

    def test_a_folder_s_stand_in_holds_an_empty_launch_file_of_its_own_name(self, adapter, cache, folder):
        path = folder / _FOLDER.name

        assert adapter.place(42, _FOLDER, str(path)) == PLACED

        assert os.readlink(path) == str(_stub(cache, 42, _FOLDER.name))
        assert path.is_dir()
        assert os.listdir(path) == ["Game.m3u"]
        assert (path / "Game.m3u").stat().st_size == 0

    def test_placing_it_again_keeps_it(self, adapter, folder):
        path = folder / _FILE.name
        adapter.place(42, _FILE, str(path))

        assert adapter.place(42, _FILE, str(path)) == KEPT
        assert path.is_symlink()

    def test_a_stub_the_cache_lost_is_made_again(self, adapter, cache, folder):
        path = folder / _FILE.name
        adapter.place(42, _FILE, str(path))
        _stub(cache, 42, _FILE.name).unlink()

        assert adapter.place(42, _FILE, str(path)) == KEPT
        assert path.is_file()

    def test_a_shape_of_the_same_name_keeps_the_link_and_remakes_the_stub(self, adapter, cache, folder):
        path = folder / "Game.m3u"
        adapter.place(42, StandInShape(name="Game.m3u", kind=FILE_SHAPE), str(path))

        assert adapter.place(42, _FOLDER, str(path)) == KEPT

        assert path.is_dir()
        assert os.listdir(_game(cache, 42)) == ["Game.m3u"]
        assert os.listdir(path) == ["Game.m3u"]

    def test_this_game_s_stand_in_pointing_at_another_stub_is_pointed_at_the_new_one(self, adapter, cache, folder):
        path = folder / _FILE.name
        path.symlink_to(_stub(cache, 42, "Old.gba"))

        assert adapter.place(42, _FILE, str(path)) == PLACED

        assert os.readlink(path) == str(_stub(cache, 42, _FILE.name))
        assert not any(name.endswith(".stand-in") for name in os.listdir(folder))

    def test_another_name_s_stub_of_the_same_game_goes(self, adapter, cache, folder):
        adapter.place(42, StandInShape(name="Old.gba", kind=FILE_SHAPE), str(folder / "Old.gba"))

        adapter.place(42, _FILE, str(folder / _FILE.name))

        assert os.listdir(_game(cache, 42)) == [_FILE.name]

    def test_a_system_folder_not_there_yet_is_created(self, adapter, tmp_path):
        path = tmp_path / "roms" / "psx" / _FILE.name

        assert adapter.place(42, _FILE, str(path)) == PLACED
        assert path.is_symlink()


class TestNeverOverAnythingElse:
    def test_a_file_of_the_user_s_is_left_as_it_is(self, adapter, folder):
        path = folder / _FILE.name
        path.write_bytes(b"the user's own dump")

        assert adapter.place(42, _FILE, str(path)) == OCCUPIED
        assert not path.is_symlink()
        assert path.read_bytes() == b"the user's own dump"

    def test_a_folder_is_left_as_it_is(self, adapter, folder):
        path = folder / _FOLDER.name
        path.mkdir()
        (path / "disc1.cue").write_text("x")

        assert adapter.place(42, _FOLDER, str(path)) == OCCUPIED
        assert os.listdir(path) == ["disc1.cue"]

    def test_a_link_of_somebody_else_s_is_left_as_it_is(self, adapter, folder, tmp_path):
        elsewhere = tmp_path / "elsewhere.gba"
        elsewhere.write_bytes(b"x")
        path = folder / _FILE.name
        path.symlink_to(elsewhere)

        assert adapter.place(42, _FILE, str(path)) == OCCUPIED
        assert os.readlink(path) == str(elsewhere)

    def test_another_game_s_stand_in_is_left_as_it_is(self, adapter, cache, folder):
        path = folder / _FILE.name
        adapter.place(7, _FILE, str(path))

        assert adapter.place(42, _FILE, str(path)) == OCCUPIED
        assert os.readlink(path) == str(_stub(cache, 7, _FILE.name))

    @pytest.mark.parametrize("name", ["..", "../x.gba", "", "a/b"])
    def test_a_name_that_is_no_single_component_is_refused(self, adapter, folder, name):
        with pytest.raises(PathTraversalError):
            adapter.place(42, StandInShape(name=name, kind=FILE_SHAPE), str(folder / "x.gba"))

    def test_a_launch_name_that_is_no_single_component_is_refused(self, adapter, folder):
        shape = StandInShape(name="Game.m3u", kind=FOLDER_SHAPE, launch_name="../Game.m3u")

        with pytest.raises(PathTraversalError):
            adapter.place(42, shape, str(folder / "Game.m3u"))


class TestRemoving:
    def test_this_game_s_stand_in_goes_and_its_stub_is_never_followed(self, adapter, cache, folder):
        path = folder / _FOLDER.name
        adapter.place(42, _FOLDER, str(path))

        assert adapter.remove(42, str(path)) is True

        assert not os.path.lexists(path)
        assert (_stub(cache, 42, _FOLDER.name) / "Game.m3u").exists()

    def test_the_stub_goes_on_its_own(self, adapter, cache, folder):
        adapter.place(42, _FOLDER, str(folder / _FOLDER.name))
        adapter.remove(42, str(folder / _FOLDER.name))

        adapter.remove_stub(42)
        adapter.remove_stub(43)

        assert not _game(cache, 42).exists()

    def test_a_file_of_the_user_s_stays(self, adapter, folder):
        path = folder / _FILE.name
        path.write_bytes(b"dump")

        assert adapter.remove(42, str(path)) is False
        assert path.read_bytes() == b"dump"

    def test_a_link_of_somebody_else_s_stays(self, adapter, folder, tmp_path):
        path = folder / _FILE.name
        path.symlink_to(tmp_path / "elsewhere.gba")

        assert adapter.remove(42, str(path)) is False
        assert path.is_symlink()

    def test_another_game_s_stand_in_stays(self, adapter, folder):
        path = folder / _FILE.name
        adapter.place(7, _FILE, str(path))

        assert adapter.remove(42, str(path)) is False
        assert path.is_symlink()

    def test_nothing_there_is_nothing_to_remove(self, adapter, folder):
        assert adapter.remove(42, str(folder / _FILE.name)) is False


class TestReading:
    def test_a_stand_in_names_its_game(self, adapter, folder):
        adapter.place(42, _FOLDER, str(folder / _FOLDER.name))

        assert adapter.stand_in_at(str(folder / _FOLDER.name)) == 42

    def test_anything_else_names_none(self, adapter, folder, tmp_path):
        (folder / "file.gba").write_bytes(b"x")
        (folder / "link.gba").symlink_to(tmp_path / "elsewhere")

        assert adapter.stand_in_at(str(folder / "file.gba")) is None
        assert adapter.stand_in_at(str(folder / "link.gba")) is None
        assert adapter.stand_in_at(str(folder / "missing.gba")) is None

    def test_a_folder_s_stand_ins_are_its_top_level_links_into_the_root(self, adapter, folder, tmp_path):
        adapter.place(42, _FILE, str(folder / _FILE.name))
        adapter.place(7, _FOLDER, str(folder / _FOLDER.name))
        (folder / "mine.gba").write_bytes(b"x")
        (folder / "link.gba").symlink_to(tmp_path / "elsewhere")
        (folder / "sub").mkdir()
        adapter.place(9, _FILE, str(folder / "sub" / _FILE.name))

        assert adapter.list_in(str(folder)) == {
            str(folder / _FILE.name): 42,
            str(folder / _FOLDER.name): 7,
        }

    def test_a_folder_that_is_not_there_holds_none(self, adapter, tmp_path):
        assert adapter.list_in(str(tmp_path / "nowhere")) == {}
