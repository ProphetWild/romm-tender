"""``EsdeGamelistFilesAdapter`` on ``tmp_path``: reading ES-DE's gamelists as they are, and writing them whole."""

from __future__ import annotations

import os
import stat

import pytest

from adapters.esde_gamelist_files import EsdeGamelistFilesAdapter

_TEXT = '<?xml version="1.0"?>\n<gameList>\n\t<game>\n\t\t<path>./A.sfc</path>\n\t</game>\n</gameList>\n'


@pytest.fixture
def files() -> EsdeGamelistFilesAdapter:
    return EsdeGamelistFilesAdapter()


@pytest.fixture
def gamelist(tmp_path):
    return tmp_path / "gamelists" / "snes" / "gamelist.xml"


class TestReading:
    def test_none_there_reads_as_none(self, files, gamelist):
        assert files.read_gamelist(str(gamelist)) is None

    def test_it_reads_the_text_byte_for_byte_line_endings_and_byte_order_mark_included(self, files, gamelist):
        gamelist.parent.mkdir(parents=True)
        gamelist.write_bytes("﻿<gameList>\r\n\t<game />\r\n</gameList>\r\n".encode())

        assert files.read_gamelist(str(gamelist)) == "﻿<gameList>\r\n\t<game />\r\n</gameList>\r\n"

    def test_a_file_that_is_no_utf_8_raises_a_value_error(self, files, gamelist):
        gamelist.parent.mkdir(parents=True)
        gamelist.write_bytes("<gameList>é</gameList>".encode("utf-16"))

        with pytest.raises(ValueError):
            files.read_gamelist(str(gamelist))

    def test_a_folder_where_the_file_should_be_raises_an_os_error(self, files, gamelist):
        gamelist.mkdir(parents=True)

        with pytest.raises(OSError):
            files.read_gamelist(str(gamelist))


class TestWriting:
    def test_it_creates_the_system_s_folder_and_writes_the_text_as_given(self, files, gamelist):
        files.write_gamelist(str(gamelist), "<gameList>\r\n</gameList>\r\n")

        assert gamelist.read_bytes() == b"<gameList>\r\n</gameList>\r\n"

    def test_it_replaces_the_file_whole_and_leaves_nothing_beside_it(self, files, gamelist):
        gamelist.parent.mkdir(parents=True)
        gamelist.write_text("a much longer gamelist than the one that replaces it", encoding="utf-8")
        inode = os.stat(gamelist).st_ino

        files.write_gamelist(str(gamelist), _TEXT)

        assert gamelist.read_text(encoding="utf-8") == _TEXT
        assert os.listdir(gamelist.parent) == ["gamelist.xml"]
        assert os.stat(gamelist).st_ino != inode

    def test_it_keeps_the_file_s_mode(self, files, gamelist):
        gamelist.parent.mkdir(parents=True)
        gamelist.write_text(_TEXT, encoding="utf-8")
        os.chmod(gamelist, 0o640)

        files.write_gamelist(str(gamelist), _TEXT + "\n")

        assert stat.S_IMODE(os.stat(gamelist).st_mode) == 0o640

    def test_a_linked_gamelist_is_written_where_the_link_points_and_the_link_stays(self, files, gamelist, tmp_path):
        kept = tmp_path / "elsewhere" / "gamelist.xml"
        kept.parent.mkdir()
        kept.write_text("<gameList />\n", encoding="utf-8")
        gamelist.parent.mkdir(parents=True)
        os.symlink(kept, gamelist)

        files.write_gamelist(str(gamelist), _TEXT)

        assert os.readlink(gamelist) == str(kept)
        assert kept.read_text(encoding="utf-8") == _TEXT
        assert os.listdir(kept.parent) == ["gamelist.xml"]

    def test_a_write_that_fails_leaves_the_file_as_it_was_and_no_temporary_file(self, files, gamelist):
        gamelist.parent.mkdir(parents=True)
        gamelist.write_text(_TEXT, encoding="utf-8")

        with pytest.raises(ValueError):
            files.write_gamelist(str(gamelist), "<gameList>\udcff</gameList>")

        assert gamelist.read_text(encoding="utf-8") == _TEXT
        assert os.listdir(gamelist.parent) == ["gamelist.xml"]

    def test_a_folder_that_cannot_be_made_raises_an_os_error(self, files, tmp_path):
        (tmp_path / "gamelists").write_text("", encoding="utf-8")

        with pytest.raises(OSError):
            files.write_gamelist(str(tmp_path / "gamelists" / "snes" / "gamelist.xml"), _TEXT)
