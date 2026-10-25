"""The path ES-DE hands its event scripts, read back as the game's own path, and which install it names.

The escaping and the command line are ES-DE's own (``fakes/esde_events.py``),
run through the real ``/bin/sh -c``, so what the inverse is held to is what a
script really receives.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

import pytest
from fakes.esde_events import ESCAPED_BY_ESDE, escaped_path, fire_event

from domain.esde_hook_path import InstalledPaths, installed_game_at, unescape_esde_path

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
def printed_first_argument(tmp_path: Path):
    """What a script ES-DE starts receives as ``$1``, for an argument ES-DE builds its command around."""
    script = tmp_path / "print-first.sh"
    script.write_text("#!/bin/sh\nprintf '%s' \"$1\"\n", encoding="utf-8")
    script.chmod(0o755)

    def run(argument: str) -> str:
        ran = fire_event(str(script), argument, "Game", "snes", "Super Nintendo", env={"PATH": "/usr/bin:/bin"})
        assert ran.returncode == 0, ran.stderr
        return ran.stdout

    return run


class TestTheEscapingIsUndone:
    @pytest.mark.parametrize("char", list(ESCAPED_BY_ESDE.replace("\\", "")))
    def test_every_character_es_de_escapes_comes_back(self, printed_first_argument, char):
        path = f"/home/deck/retrodeck/roms/snes/a{char}b{char}.sfc"

        arrived = printed_first_argument(escaped_path(path))

        assert unescape_esde_path(arrived) == path

    def test_the_shell_takes_away_only_the_backslash_before_a_quote_and_a_dollar(self, printed_first_argument):
        """What arrives still carries ES-DE's other backslashes, which is what the inverse is for."""
        arrived = printed_first_argument(escaped_path("/roms/a b'c\"d$e(f).sfc"))

        assert arrived == "/roms/a\\ b\\'c\"d$e\\(f\\).sfc"

    @pytest.mark.parametrize(
        "path",
        [
            "/home/deck/retrodeck/roms/psx/Final Fantasy VII (USA) [Disc 1].m3u/Final Fantasy VII (USA) [Disc 1].m3u",
            "/roms/snes/Don't Stop! (Rev 1) & {Beta} <proto> ; ok?.sfc",
            "/roms/snes/$HOME $(id) ${PATH} ^caret*star.sfc",
            "/roms/gba/Pokémon \u2013 Édition Rubis (France) ★.gba",
            "/roms/gba/a  two  spaces .gba",
            "/roms/gba/plain.gba",
        ],
    )
    def test_a_whole_path_comes_back(self, printed_first_argument, path):
        assert unescape_esde_path(printed_first_argument(escaped_path(path))) == path

    def test_a_path_with_nothing_escaped_is_itself(self):
        assert unescape_esde_path("/roms/gba/plain.gba") == "/roms/gba/plain.gba"

    @pytest.mark.parametrize("arrived", ["/roms/a\\zb.gba", "/roms/a\\", "\\", "/roms/a\\-b.gba"])
    def test_a_backslash_es_de_would_not_have_left_stays(self, arrived):
        assert unescape_esde_path(arrived) == arrived

    def test_an_empty_path_is_empty(self):
        assert unescape_esde_path("") == ""


def _install(rom_id: int, file_path: str, rom_dir: str | None = None) -> InstalledPaths:
    return InstalledPaths(rom_id=rom_id, file_path=file_path, rom_dir=rom_dir)


def _at(path: str, installs: list[InstalledPaths]) -> int | None:
    return installed_game_at(path, os.path.dirname(path), installs)


class TestWhichInstallAPathNames:
    _SINGLE = _install(1, "/roms/gba/Game (USA).gba")
    _FOLDER = _install(2, "/roms/psx/FF7.m3u/FF7.m3u", "/roms/psx/FF7.m3u")
    _UNCOLLAPSED = _install(3, "/roms/psx/Other/Other.m3u", "/roms/psx/Other")

    def test_an_installs_recorded_file(self):
        assert _at("/roms/gba/Game (USA).gba", [self._SINGLE, self._FOLDER]) == 1

    def test_the_file_es_de_names_inside_a_folder_it_reads_as_a_file(self):
        assert _at("/roms/psx/FF7.m3u/FF7.m3u", [self._SINGLE, self._FOLDER]) == 2

    def test_an_installs_folder_itself(self):
        assert _at("/roms/psx/FF7.m3u", [self._SINGLE, self._FOLDER]) == 2

    def test_another_file_directly_in_an_installs_folder(self):
        assert _at("/roms/psx/Other/Other (Disc 2).cue", [self._UNCOLLAPSED]) == 3

    def test_a_file_deeper_in_an_installs_folder_is_none_of_its(self):
        assert _at("/roms/psx/Other/extras/manual.cue", [self._UNCOLLAPSED]) is None

    def test_a_file_beside_a_single_file_install_is_none_of_its(self):
        assert _at("/roms/gba/Game (Europe).gba", [self._SINGLE]) is None

    def test_a_path_no_install_names(self):
        assert _at("/roms/snes/Unknown.sfc", [self._SINGLE, self._FOLDER, self._UNCOLLAPSED]) is None

    def test_no_installs_name_nothing(self):
        assert _at("/roms/gba/Game (USA).gba", []) is None

    def test_a_recorded_file_wins_over_a_folder_that_holds_it(self):
        holder = _install(4, "/roms/psx/Box/Box.m3u", "/roms/psx/Box")
        inner = _install(5, "/roms/psx/Box/Inner.cue")

        assert _at("/roms/psx/Box/Inner.cue", [holder, inner]) == 5

    def test_the_paths_are_compared_as_handed_in(self):
        """Resolving symlinks is the caller's: a spelling through a link is a different string here."""
        assert installed_game_at("/home/deck/roms/gba/Game (USA).gba", "/home/deck/roms/gba", [self._SINGLE]) is None
