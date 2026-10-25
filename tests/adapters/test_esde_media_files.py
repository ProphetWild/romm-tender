"""``EsdeMediaFilesAdapter`` on a real filesystem: Tender's copies of RomM's media, and the links ES-DE reads them by.

Each test lays out a cache root and an ES-DE media tree under ``tmp_path``. A
link is Tender's exactly when its target lies below ``<cache>/esde/``; the
tests put the user's own files and other programs' links beside Tender's and
read back that only Tender's ever change.
"""

from __future__ import annotations

import os

import pytest

from adapters.esde_media_files import EsdeMediaFilesAdapter
from domain.esde_media_record import HELD, LINKED, UNCHANGED

_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


@pytest.fixture
def cache(tmp_path) -> str:
    return str(tmp_path / "cache")


@pytest.fixture
def files(cache) -> EsdeMediaFilesAdapter:
    return EsdeMediaFilesAdapter(cache_dir=cache)


@pytest.fixture
def covers(tmp_path) -> str:
    folder = tmp_path / "downloaded_media" / "gba" / "covers"
    return str(folder)


def _names(covers: str, stem: str = "Game") -> list[str]:
    return [os.path.join(covers, stem + extension) for extension in (".png", ".jpg", ".webp")]


def _kept(files: EsdeMediaFilesAdapter, rom_id: int = 7, extension: str = ".png", *, on_demand: bool = False) -> int:
    folder = "videos" if on_demand else "covers"
    staged = files.stage(rom_id, folder, on_demand=on_demand)
    with open(staged, "wb") as handle:
        handle.write(_PNG)
    return files.keep(staged, rom_id, folder, extension, on_demand=on_demand)


def _link(files: EsdeMediaFilesAdapter, covers: str, extension: str = ".png", rom_id: int = 7) -> str:
    return files.link(
        rom_id,
        "covers",
        extension,
        on_demand=False,
        link_path=os.path.join(covers, "Game" + extension),
        names=_names(covers),
    )


def _write(path: str, data: bytes = b"the user's own") -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as handle:
        handle.write(data)


def _read(path: str) -> bytes:
    with open(path, "rb") as handle:
        return handle.read()


class TestKeepingACopy:
    def test_a_staged_file_is_kept_in_the_cache_root_under_its_folder_and_extension(self, files, cache):
        size = _kept(files)

        kept = os.path.join(os.path.realpath(cache), "esde", "media", "7", "covers.png")
        assert size == len(_PNG)
        assert _read(kept) == _PNG
        assert files.kept_size(7, "covers", ".png", on_demand=False) == len(_PNG)
        assert os.listdir(os.path.dirname(kept)) == ["covers.png"]

    def test_a_copy_fetched_on_selection_is_kept_apart(self, files, cache):
        _kept(files, on_demand=True)

        assert os.path.isfile(os.path.join(os.path.realpath(cache), "esde", "on-demand", "7", "videos.png"))
        assert files.kept_size(7, "videos", ".png", on_demand=False) is None

    def test_a_copy_under_another_extension_takes_the_earlier_one_s_place(self, files):
        _kept(files, extension=".png")

        _kept(files, extension=".jpg")

        assert files.kept_size(7, "covers", ".png", on_demand=False) is None
        assert files.kept_size(7, "covers", ".jpg", on_demand=False) == len(_PNG)

    def test_the_head_is_the_start_of_the_file(self, files):
        staged = files.stage(7, "covers", on_demand=False)
        _write(staged, _PNG + b"rest")

        assert files.head(staged) == _PNG[:64]

    def test_a_staged_file_discarded_is_gone_and_one_not_there_is_nothing_to_do(self, files):
        staged = files.stage(7, "covers", on_demand=False)
        _write(staged, _PNG)

        files.discard(staged)
        files.discard(staged)

        assert not os.path.exists(staged)

    def test_nothing_kept_has_no_size(self, files):
        assert files.kept_size(7, "covers", ".png", on_demand=False) is None

    def test_forgetting_a_folder_removes_its_copy_under_every_extension(self, files):
        _kept(files)

        files.forget(7, "covers", on_demand=False)

        assert files.kept_size(7, "covers", ".png", on_demand=False) is None

    def test_forgetting_a_game_removes_both_its_caches(self, files, cache):
        _kept(files)
        _kept(files, on_demand=True)
        _kept(files, rom_id=8)

        files.forget_game(7)

        root = os.path.join(os.path.realpath(cache), "esde")
        assert not os.path.exists(os.path.join(root, "media", "7"))
        assert not os.path.exists(os.path.join(root, "on-demand", "7"))
        assert files.kept_size(8, "covers", ".png", on_demand=False) == len(_PNG)

    def test_forgetting_everything_leaves_the_stand_ins_alone(self, files, cache):
        _kept(files)
        _kept(files, on_demand=True)
        stub = os.path.join(cache, "esde", "stand-ins", "7", "Game.gba")
        _write(stub, b"")

        files.forget_all()

        assert files.kept_size(7, "covers", ".png", on_demand=False) is None
        assert files.kept_size(7, "videos", ".png", on_demand=True) is None
        assert os.path.isfile(stub)


class TestLinking:
    def test_a_free_slot_gets_an_absolute_link_to_the_copy(self, files, covers, cache):
        _kept(files)

        outcome = _link(files, covers)

        link = os.path.join(covers, "Game.png")
        assert outcome == LINKED
        assert os.readlink(link) == os.path.join(os.path.realpath(cache), "esde", "media", "7", "covers.png")
        assert _read(link) == _PNG

    def test_the_link_already_there_is_unchanged(self, files, covers):
        _kept(files)
        _link(files, covers)

        assert _link(files, covers) == UNCHANGED

    def test_a_file_of_the_user_s_at_the_name_is_never_replaced(self, files, covers):
        _kept(files)
        _write(os.path.join(covers, "Game.png"))

        assert _link(files, covers) == HELD
        assert _read(os.path.join(covers, "Game.png")) == b"the user's own"

    def test_a_file_of_the_user_s_under_another_extension_holds_the_slot(self, files, covers):
        _kept(files)
        _write(os.path.join(covers, "Game.jpg"))

        assert _link(files, covers) == HELD
        assert not os.path.lexists(os.path.join(covers, "Game.png"))
        assert _read(os.path.join(covers, "Game.jpg")) == b"the user's own"

    def test_another_program_s_link_holds_the_slot(self, files, covers, tmp_path):
        _kept(files)
        elsewhere = str(tmp_path / "scraper" / "Game.webp")
        _write(elsewhere, b"scraped")
        os.makedirs(covers)
        os.symlink(elsewhere, os.path.join(covers, "Game.webp"))

        assert _link(files, covers) == HELD
        assert os.readlink(os.path.join(covers, "Game.webp")) == elsewhere

    def test_a_slot_the_user_fills_later_takes_tender_s_link_away(self, files, covers):
        _kept(files)
        _link(files, covers)
        _write(os.path.join(covers, "Game.jpg"))

        assert _link(files, covers) == HELD
        assert not os.path.lexists(os.path.join(covers, "Game.png"))
        assert _read(os.path.join(covers, "Game.jpg")) == b"the user's own"

    def test_tender_s_link_under_another_extension_goes_once_the_new_one_stands(self, files, covers):
        _kept(files, extension=".png")
        _link(files, covers, ".png")
        _kept(files, extension=".jpg")

        outcome = _link(files, covers, ".jpg")

        assert outcome == LINKED
        assert not os.path.lexists(os.path.join(covers, "Game.png"))
        assert _read(os.path.join(covers, "Game.jpg")) == _PNG

    def test_a_broken_link_of_tender_s_is_replaced(self, files, covers, cache):
        _kept(files)
        os.makedirs(covers)
        gone = os.path.join(os.path.realpath(cache), "esde", "media", "7", "gone.png")
        os.symlink(gone, os.path.join(covers, "Game.png"))

        assert _link(files, covers) == LINKED
        assert _read(os.path.join(covers, "Game.png")) == _PNG

    def test_another_game_s_link_at_the_name_is_tender_s_and_replaced(self, files, covers):
        _kept(files, rom_id=8)
        _link(files, covers, rom_id=8)
        _kept(files, rom_id=7)

        assert _link(files, covers, rom_id=7) == LINKED
        assert "/7/" in os.readlink(os.path.join(covers, "Game.png"))


class TestUnlinking:
    def test_a_game_s_own_link_goes(self, files, covers):
        _kept(files)
        _link(files, covers)

        assert files.unlink(7, os.path.join(covers, "Game.png")) is True
        assert not os.path.lexists(os.path.join(covers, "Game.png"))

    def test_another_game_s_link_and_the_user_s_file_stay(self, files, covers):
        _kept(files, rom_id=8)
        _link(files, covers, rom_id=8)
        _write(os.path.join(covers, "Mine.png"))

        assert files.unlink(7, os.path.join(covers, "Game.png")) is False
        assert files.unlink(7, os.path.join(covers, "Mine.png")) is False
        assert files.unlink(7, os.path.join(covers, "Nothing.png")) is False
        assert os.path.islink(os.path.join(covers, "Game.png"))
        assert _read(os.path.join(covers, "Mine.png")) == b"the user's own"

    def test_unlinking_all_removes_every_link_of_tender_s_and_nothing_else(self, files, covers, tmp_path):
        _kept(files, rom_id=7)
        _kept(files, rom_id=8)
        _link(files, covers, rom_id=7)
        marquees = str(tmp_path / "downloaded_media" / "gba" / "marquees")
        other = os.path.join(marquees, "Other.png")
        files.link(8, "covers", ".png", on_demand=False, link_path=other, names=[other])
        _write(os.path.join(covers, "Mine.png"))
        elsewhere = str(tmp_path / "scraper.png")
        _write(elsewhere, b"scraped")
        os.symlink(elsewhere, os.path.join(covers, "Scraped.png"))

        removed = files.unlink_all(str(tmp_path / "downloaded_media"))

        assert removed == 2
        assert sorted(os.listdir(covers)) == ["Mine.png", "Scraped.png"]
        assert os.listdir(marquees) == []

    def test_unlinking_all_where_there_is_no_media_tree_removes_nothing(self, files, tmp_path):
        assert files.unlink_all(str(tmp_path / "missing")) == 0
