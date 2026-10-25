"""Tender's copies of RomM's media under the cache root, and the links ES-DE's media tree gets to them.

Owns every file operation on ES-DE's media: staging a fetch, keeping its copy
at ``<cache>/esde/media/<rom_id>/<folder><ext>`` (or ``on-demand/`` for a
folder fetched on selection), and the absolute links ES-DE reads them through.
Which files go where, and when, is the media service's; what ES-DE reads is
``domain/esde_media.py``'s.

**A link is Tender's exactly when its target lies below ``<cache>/esde/``**
(docs/specs/esde-library.md, D11), and nothing but such a link is ever
replaced or removed. Each name is read again without being followed
(``lstat``, ``readlink``) right before it is changed. Anything else at a name
ES-DE reads for a game and folder — a file, a folder, another program's link —
holds that slot: Tender links nothing there and never overwrites, renames or
removes it.
"""

from __future__ import annotations

import contextlib
import os
import shutil
import stat
from typing import TYPE_CHECKING

from domain.esde_media import SNIFF_LENGTH, lookup_order
from domain.esde_media_record import HELD, LINKED, UNCHANGED, LinkOutcome
from lib.path_safety import safe_path_component

if TYPE_CHECKING:
    from collections.abc import Sequence

    from domain.esde_media import MediaFolder

_ESDE = "esde"
_SYNCED = "media"
_ON_DEMAND = "on-demand"
_STAGED = ".part"


class EsdeMediaFilesAdapter:
    """Keeps media copies below *cache_dir* and links ES-DE's media tree to them.

    Implements the ``EsdeMediaStore`` Protocol structurally. The cache root is
    symlink-resolved once, here, and every link is written with that
    spelling, so a link is recognised by the same string it was written with.
    """

    def __init__(self, *, cache_dir: str) -> None:
        self._root = os.path.realpath(os.path.join(cache_dir, _ESDE))

    def stage(self, rom_id: int, folder: MediaFolder, *, on_demand: bool) -> str:
        """A path to fetch *rom_id*'s *folder* file into, in the folder its copy is kept in, which it creates."""
        game = self._game_dir(rom_id, on_demand=on_demand)
        os.makedirs(game, exist_ok=True)
        return os.path.join(game, f".{safe_path_component(folder)}{_STAGED}")

    def head(self, path: str) -> bytes:
        """The first ``SNIFF_LENGTH`` bytes of the file at *path*, or all of a shorter one."""
        with open(path, "rb") as handle:
            return handle.read(SNIFF_LENGTH)

    def discard(self, path: str) -> None:
        """Remove the staged file at *path*; one not there, or that cannot be removed, is left."""
        with contextlib.suppress(OSError):
            os.unlink(path)

    def keep(self, staged: str, rom_id: int, folder: MediaFolder, extension: str, *, on_demand: bool) -> int:
        """Keep *staged* as *rom_id*'s *folder* copy with *extension*, in place of one kept under another; its size.

        Raises ``OSError`` where it cannot be moved into place.
        """
        kept = self._kept_path(rom_id, folder, extension, on_demand=on_demand)
        os.replace(staged, kept)
        for other in lookup_order(folder):
            if other != extension:
                with contextlib.suppress(FileNotFoundError):
                    os.unlink(self._kept_path(rom_id, folder, other, on_demand=on_demand))
        return os.lstat(kept).st_size

    def kept_size(self, rom_id: int, folder: MediaFolder, extension: str, *, on_demand: bool) -> int | None:
        """The size of *rom_id*'s *folder* copy kept with *extension*, or ``None`` where no such file is kept."""
        try:
            info = os.lstat(self._kept_path(rom_id, folder, extension, on_demand=on_demand))
        except (OSError, ValueError):
            return None
        return info.st_size if stat.S_ISREG(info.st_mode) else None

    def forget(self, rom_id: int, folder: MediaFolder, *, on_demand: bool) -> None:
        """Remove *rom_id*'s kept *folder* copy under every extension, and a fetch of it staged."""
        for extension in lookup_order(folder):
            with contextlib.suppress(FileNotFoundError):
                os.unlink(self._kept_path(rom_id, folder, extension, on_demand=on_demand))
        self.discard(os.path.join(self._game_dir(rom_id, on_demand=on_demand), f".{folder}{_STAGED}"))

    def forget_game(self, rom_id: int) -> None:
        """Remove every copy kept for *rom_id*, in both caches."""
        for on_demand in (False, True):
            _remove_tree(self._game_dir(rom_id, on_demand=on_demand))

    def forget_all(self) -> None:
        """Remove every copy kept for any game; the stand-ins' stubs beside them stay."""
        for cache in (_SYNCED, _ON_DEMAND):
            _remove_tree(os.path.join(self._root, cache))

    def link(
        self,
        rom_id: int,
        folder: MediaFolder,
        extension: str,
        *,
        on_demand: bool,
        link_path: str,
        names: Sequence[str],
    ) -> LinkOutcome:
        """Link *link_path* to *rom_id*'s kept *folder* copy, unless something not Tender's holds one of *names*.

        *names* are every path ES-DE reads for that game and folder,
        *link_path* among them. Where one is held, every link of Tender's among
        them goes and :data:`HELD` is answered; otherwise, once the link stands,
        Tender's links at the other names go. A free path takes ``os.symlink``
        straight there, which fails rather than replace whatever appeared
        there in between; the road not taken is a temporary link renamed over
        the path, which would replace such a file silently. Only a link of
        Tender's, already checked, is replaced that way, so no reader ever sees
        the path empty. Raises ``OSError`` where a link cannot be written or
        removed.
        """
        target = self._kept_path(rom_id, folder, extension, on_demand=on_demand)
        if any(self._held(name) for name in names):
            self._unlink_tenders(names)
            return HELD
        os.makedirs(os.path.dirname(link_path), exist_ok=True)
        try:
            os.symlink(target, link_path)
        except FileExistsError:
            outcome = self._relink(target, link_path)
        else:
            outcome = LINKED
        if outcome == HELD:
            self._unlink_tenders(names)
        else:
            self._unlink_tenders([name for name in names if name != link_path])
        return outcome

    def unlink(self, rom_id: int, link_path: str) -> bool:
        """Remove the link at *link_path* if it is Tender's link to one of *rom_id*'s copies; whether it went.

        Raises ``OSError`` where such a link cannot be removed.
        """
        target = self._tenders_target(link_path)
        games = (self._game_dir(rom_id, on_demand=False), self._game_dir(rom_id, on_demand=True))
        if target is None or not any(_below(target, game) for game in games):
            return False
        os.unlink(link_path)
        return True

    def unlink_all(self, media_root: str) -> int:
        """Remove every link of Tender's below *media_root*, never following one; how many went.

        A link that cannot be removed is left and not counted.
        """
        removed = 0
        for directory, folders, names in os.walk(media_root):
            for name in folders + names:
                path = os.path.join(directory, name)
                if self._tenders_target(path) is None:
                    continue
                with contextlib.suppress(OSError):
                    os.unlink(path)
                    removed += 1
        return removed

    def _relink(self, target: str, link_path: str) -> LinkOutcome:
        """Point Tender's link at *link_path* at *target*; :data:`HELD` where what is there is not Tender's."""
        current = self._tenders_target(link_path)
        if current is None:
            return HELD
        if current == target:
            return UNCHANGED
        temporary = os.path.join(os.path.dirname(link_path), f".{os.path.basename(link_path)}.{os.getpid()}.media")
        with contextlib.suppress(FileNotFoundError):
            os.unlink(temporary)
        os.symlink(target, temporary)
        try:
            os.replace(temporary, link_path)
        except BaseException:
            with contextlib.suppress(OSError):
                os.unlink(temporary)
            raise
        return LINKED

    def _held(self, path: str) -> bool:
        """Whether something that is not Tender's link is at *path*."""
        try:
            os.lstat(path)
        except FileNotFoundError:
            return False
        return self._tenders_target(path) is None

    def _unlink_tenders(self, names: Sequence[str]) -> None:
        for name in names:
            if self._tenders_target(name) is not None:
                with contextlib.suppress(FileNotFoundError):
                    os.unlink(name)

    def _tenders_target(self, path: str) -> str | None:
        """The target of the link at *path* where it is Tender's, never following it; else ``None``."""
        try:
            if not stat.S_ISLNK(os.lstat(path).st_mode):
                return None
            target = os.readlink(path)
        except OSError:
            return None
        if not os.path.isabs(target) or not _below(os.path.normpath(target), self._root):
            return None
        return os.path.normpath(target)

    def _game_dir(self, rom_id: int, *, on_demand: bool) -> str:
        return os.path.join(self._root, _ON_DEMAND if on_demand else _SYNCED, str(int(rom_id)))

    def _kept_path(self, rom_id: int, folder: MediaFolder, extension: str, *, on_demand: bool) -> str:
        return os.path.join(self._game_dir(rom_id, on_demand=on_demand), safe_path_component(folder + extension))


def _below(path: str, folder: str) -> bool:
    return path.startswith(folder.rstrip(os.sep) + os.sep)


def _remove_tree(path: str) -> None:
    """Remove the folder at *path* with everything in it; none there is nothing to do."""
    with contextlib.suppress(FileNotFoundError):
        if stat.S_ISDIR(os.lstat(path).st_mode):
            shutil.rmtree(path)
