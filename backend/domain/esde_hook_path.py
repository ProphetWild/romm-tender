"""The game path ES-DE hands its event scripts, read back as the path it names, and the install it names.

ES-DE passes ``game-start`` and ``game-end`` the ROM path as
``Utils::FileSystem::getEscapedPath`` makes it (es-core/src/utils/FileSystemUtil.cpp):
every backslash turned into a slash, then a backslash put before each space and
each of ``\\ ' " ! $ ^ & * ( ) { } [ ] ? ; < >``. ``Scripting::fireEvent``
(es-core/src/Scripting.cpp) wraps that in double quotes and hands the line to
``system()``, and inside double quotes the shell takes away only a backslash
before ``"``, ``$``, a backtick, a backslash or a newline (POSIX, "Double-Quotes"),
so what reaches the script still carries the backslash before every other
character escaped. A backtick is not escaped at all, so the shell runs what
lies between two of them, and such a path never reaches a script intact.
``game-select`` is passed the path unescaped.

A path ES-DE names is a link's own path, never its target, and for a folder it
reads as a file the file of that name inside it (``FileData::launchGame``,
es-app/src/FileData.cpp), so an install is matched by its recorded file, its
folder, or a file directly in its folder.

Pure compute — no I/O.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable

# getEscapedPath's characters whose backslash the double quotes leave in place:
# all of them but '"' and '$'. A backslash of the path's own never gets this far.
_STILL_ESCAPED = frozenset(" '!^&*(){}[]?;<>")


def unescape_esde_path(arrived: str) -> str:
    """The path ES-DE escaped into *arrived*, as a script's ``$1`` receives it on ``game-start`` and ``game-end``.

    Each backslash before a character the shell left escaped goes; any other
    backslash stays, since ES-DE leaves none that is not an escape.
    """
    kept: list[str] = []
    index = 0
    while index < len(arrived):
        char = arrived[index]
        if char == "\\" and index + 1 < len(arrived) and arrived[index + 1] in _STILL_ESCAPED:
            kept.append(arrived[index + 1])
            index += 2
            continue
        kept.append(char)
        index += 1
    return "".join(kept)


@dataclass(frozen=True, slots=True)
class InstalledPaths:
    """Where one install lies: its recorded launch file and, for a game in a folder of its own, that folder."""

    rom_id: int
    file_path: str
    rom_dir: str | None


def installed_game_at(path: str, folder: str, installs: Iterable[InstalledPaths]) -> int | None:
    """The game whose install *path* names, or ``None``.

    *path* names an install when it is its recorded file, its folder, or a file
    directly in that folder; *folder* is the folder *path* lies in. A recorded
    file is matched before any folder. Every path is compared as it is handed
    in, so a caller resolves the symlinks in all of them first.
    """
    listed = tuple(installs)
    for install in listed:
        if path == install.file_path:
            return install.rom_id
    for install in listed:
        if install.rom_dir is not None and install.rom_dir in (path, folder):
            return install.rom_id
    return None
