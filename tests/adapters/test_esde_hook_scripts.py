"""``EsdeHookScriptsAdapter`` on a real ``tmp_path``: Tender's scripts written and removed, nobody else's touched."""

from __future__ import annotations

import os
import stat
from typing import TYPE_CHECKING

import pytest

from adapters.esde_hook_scripts import EsdeHookScriptsAdapter
from domain.esde_hook_scripts import MARKER

if TYPE_CHECKING:
    from pathlib import Path

_OURS = f"#!/bin/sh\n{MARKER}\nexit 0\n"
_NEWER = f"#!/bin/sh\n{MARKER}\necho newer\nexit 0\n"
_THEIRS = "#!/bin/sh\necho the user's own\n"


@pytest.fixture
def adapter() -> EsdeHookScriptsAdapter:
    return EsdeHookScriptsAdapter()


@pytest.fixture
def path(tmp_path: Path) -> Path:
    return tmp_path / "ES-DE" / "scripts" / "game-start" / "tender-romm.sh"


def _mode(path: Path) -> int:
    return stat.S_IMODE(os.lstat(path).st_mode)


class TestInstalling:
    def test_a_script_is_written_executable_into_its_events_folder_made_for_it(self, adapter, path):
        assert adapter.install(str(path), _OURS) is True

        assert path.read_text(encoding="utf-8") == _OURS
        assert _mode(path) == 0o755

    def test_tenders_own_script_is_replaced(self, adapter, path):
        adapter.install(str(path), _OURS)

        assert adapter.install(str(path), _NEWER) is True

        assert path.read_text(encoding="utf-8") == _NEWER

    def test_a_script_tender_did_not_write_is_left_as_it_is(self, adapter, path):
        path.parent.mkdir(parents=True)
        path.write_text(_THEIRS, encoding="utf-8")
        path.chmod(0o700)

        assert adapter.install(str(path), _OURS) is False

        assert path.read_text(encoding="utf-8") == _THEIRS
        assert _mode(path) == 0o700

    def test_a_link_at_the_name_is_left_and_never_followed(self, adapter, path, tmp_path):
        target = tmp_path / "elsewhere.sh"
        target.write_text(_OURS, encoding="utf-8")
        path.parent.mkdir(parents=True)
        path.symlink_to(target)

        assert adapter.install(str(path), _NEWER) is False

        assert path.is_symlink()
        assert target.read_text(encoding="utf-8") == _OURS

    def test_a_folder_at_the_name_is_left(self, adapter, path):
        path.mkdir(parents=True)

        assert adapter.install(str(path), _OURS) is False

        assert path.is_dir()

    def test_nothing_is_left_beside_it(self, adapter, path):
        adapter.install(str(path), _OURS)
        adapter.install(str(path), _NEWER)

        assert sorted(os.listdir(path.parent)) == ["tender-romm.sh"]

    def test_a_write_that_fails_leaves_neither_a_half_script_nor_its_temporary(self, adapter, path, monkeypatch):
        adapter.install(str(path), _OURS)

        def refuse(*_args: object) -> None:
            raise OSError("no space left")

        monkeypatch.setattr(os, "replace", refuse)

        with pytest.raises(OSError, match="no space left"):
            adapter.install(str(path), _NEWER)

        assert path.read_text(encoding="utf-8") == _OURS
        assert sorted(os.listdir(path.parent)) == ["tender-romm.sh"]

    def test_the_other_scripts_in_the_folder_are_left_alone(self, adapter, path):
        path.parent.mkdir(parents=True)
        other = path.parent / "01-mine.sh"
        other.write_text(_THEIRS, encoding="utf-8")

        adapter.install(str(path), _OURS)

        assert other.read_text(encoding="utf-8") == _THEIRS


class TestRemoving:
    def test_tenders_script_is_removed(self, adapter, path):
        adapter.install(str(path), _OURS)

        assert adapter.remove(str(path)) is True

        assert not os.path.lexists(path)

    def test_a_script_tender_did_not_write_stays(self, adapter, path):
        path.parent.mkdir(parents=True)
        path.write_text(_THEIRS, encoding="utf-8")

        assert adapter.remove(str(path)) is False

        assert path.read_text(encoding="utf-8") == _THEIRS

    def test_a_link_to_a_script_of_tenders_stays(self, adapter, path, tmp_path):
        target = tmp_path / "elsewhere.sh"
        target.write_text(_OURS, encoding="utf-8")
        path.parent.mkdir(parents=True)
        path.symlink_to(target)

        assert adapter.remove(str(path)) is False

        assert path.is_symlink()
        assert target.exists()

    def test_the_marker_anywhere_but_its_own_line_is_no_marker(self, adapter, path):
        path.parent.mkdir(parents=True)
        path.write_text(f"#!/bin/sh\necho hi\n{MARKER}\n", encoding="utf-8")

        assert adapter.remove(str(path)) is False

        assert path.exists()

    def test_nothing_there_is_nothing_to_remove(self, adapter, path):
        assert adapter.remove(str(path)) is False

    def test_a_file_that_is_not_text_is_nobody_s_script_of_tenders(self, adapter, path):
        path.parent.mkdir(parents=True)
        path.write_bytes(b"\xff\xfe\x00binary")

        assert adapter.remove(str(path)) is False

        assert path.exists()
