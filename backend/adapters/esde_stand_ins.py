"""The stand-ins in RetroDECK's ROM folders, and their stubs under Tender's cache root.

Owns every file operation on a stand-in: making its stub, placing its link,
reading which game a link stands for, and removing a link. What a stand-in is,
and how its target names its game, is ``domain/esde_stand_in.py``'s; which game
gets one, where, and when it goes, is the service's.

**Nothing but a stand-in is ever replaced or removed.** Before a link is
replaced or removed it is read again without being followed (``lstat``,
``readlink``), and it goes only while it is a link whose target lies below the
stand-in root and names the game it is removed for. A file, a folder or a link
of anybody else's at the path is left exactly as it is.
"""

from __future__ import annotations

import contextlib
import os
import shutil
import stat

from domain.esde_stand_in import (
    FOLDER_SHAPE,
    KEPT,
    OCCUPIED,
    PLACED,
    PlaceOutcome,
    StandInShape,
    stand_in_rom_id,
    stand_in_root,
)
from lib.path_safety import safe_join, safe_path_component


class EsdeStandInsAdapter:
    """Places, reads and removes stand-ins, their stubs kept below *cache_dir*.

    Implements the ``EsdeStandInStore`` Protocol structurally. The stand-in root
    is symlink-resolved once, here, and every link is written with that
    spelling, so a link is recognised by the same string it was written with.
    """

    def __init__(self, *, cache_dir: str) -> None:
        self._root = os.path.realpath(stand_in_root(cache_dir))

    def place(self, rom_id: int, shape: StandInShape, path: str) -> PlaceOutcome:
        """Make *rom_id*'s stub for *shape* and its link at *path* — where nothing is there, or this game's stand-in is.

        Answers :data:`PLACED` for a link made or pointed at a new stub,
        :data:`KEPT` for this game's link already pointing at it, and
        :data:`OCCUPIED` where anything else is at *path*. Raises
        ``PathTraversalError`` for a name that is no single path component, and
        ``OSError`` where the stub or the link cannot be written.

        A path nothing is at takes ``os.symlink`` straight there, which fails
        rather than replace whatever appeared there in between; the road not
        taken is the temporary link renamed over the path, which would replace
        such a file silently. Only this game's own link, already checked, is
        replaced that way, so no reader ever sees the path empty.
        """
        target = self._make_stub(rom_id, shape)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        try:
            os.symlink(target, path)
        except FileExistsError:
            pass
        else:
            return PLACED
        if self.stand_in_at(path) != rom_id:
            return OCCUPIED
        if os.readlink(path) == target:
            return KEPT
        temporary = os.path.join(os.path.dirname(path), f".{os.path.basename(path)}.{os.getpid()}.stand-in")
        with contextlib.suppress(FileNotFoundError):
            os.unlink(temporary)
        os.symlink(target, temporary)
        try:
            os.replace(temporary, path)
        except BaseException:
            with contextlib.suppress(OSError):
                os.unlink(temporary)
            raise
        return PLACED

    def remove(self, rom_id: int, path: str) -> bool:
        """Remove the link at *path* if it is *rom_id*'s stand-in, never following it; whether it was removed.

        The stub stays (:meth:`remove_stub`). Raises ``OSError`` where a
        stand-in that is there cannot be removed.
        """
        if self.stand_in_at(path) != rom_id:
            return False
        os.unlink(path)
        return True

    def remove_stub(self, rom_id: int) -> None:
        """Remove *rom_id*'s stubs from the cache root; none there is nothing to do."""
        game = os.path.join(self._root, str(rom_id))
        with contextlib.suppress(FileNotFoundError):
            if stat.S_ISDIR(os.lstat(game).st_mode):
                shutil.rmtree(game)

    def stand_in_at(self, path: str) -> int | None:
        """The game the link at *path* stands in for, or ``None`` where it is no stand-in, never following it."""
        try:
            if not stat.S_ISLNK(os.lstat(path).st_mode):
                return None
            return stand_in_rom_id(os.readlink(path), self._root)
        except OSError:
            return None

    def list_in(self, folder: str) -> dict[str, int]:
        """Every stand-in directly in *folder*, by path, with the game it stands in for; none where it is not there."""
        try:
            entries = list(os.scandir(folder))
        except (FileNotFoundError, NotADirectoryError):
            return {}
        found: dict[str, int] = {}
        for entry in entries:
            rom_id = self.stand_in_at(entry.path) if entry.is_symlink() else None
            if rom_id is not None:
                found[entry.path] = rom_id
        return found

    def _make_stub(self, rom_id: int, shape: StandInShape) -> str:
        """Make *rom_id*'s stub for *shape* — its folder holding nothing else — and answer its path."""
        name = safe_path_component(shape.name)
        game = os.path.join(self._root, str(rom_id))
        os.makedirs(game, exist_ok=True)
        _clear_beside(game, keep=name)
        stub = safe_join(game, name)
        if shape.kind == FOLDER_SHAPE:
            launch = safe_path_component(shape.launch_name or "")
            _replace_unless(stub, is_folder=True)
            os.makedirs(stub, exist_ok=True)
            _clear_beside(stub, keep=launch)
            _touch(os.path.join(stub, launch))
        else:
            _replace_unless(stub, is_folder=False)
            _touch(stub)
        return stub


def _clear_beside(folder: str, *, keep: str) -> None:
    """Remove every entry of *folder*, a folder of the stand-in root's, but *keep*."""
    for entry in os.scandir(folder):
        if entry.name != keep:
            _remove_entry(entry.path)


def _replace_unless(path: str, *, is_folder: bool) -> None:
    """Remove what is at *path*, an entry of the stand-in root's, unless it is a real folder or file as asked."""
    try:
        mode = os.lstat(path).st_mode
    except FileNotFoundError:
        return
    if stat.S_ISDIR(mode) if is_folder else stat.S_ISREG(mode):
        return
    _remove_entry(path)


def _remove_entry(path: str) -> None:
    """Remove *path* — a link or file as itself, a real folder with everything in it."""
    if stat.S_ISDIR(os.lstat(path).st_mode):
        shutil.rmtree(path)
    else:
        os.unlink(path)


def _touch(path: str) -> None:
    """Create an empty file at *path*, leaving one already there as it is."""
    os.close(os.open(path, os.O_CREAT | os.O_WRONLY, 0o644))
