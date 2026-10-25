"""ES-DE's event scripts of Tender's: written while the library surface writes ES-DE, removed once it does not.

What each script says and where it goes is ``domain/esde_hook_scripts.py``'s,
and every file operation is the ``EsdeHookScriptStore``'s, which never replaces
or removes a script that is not Tender's. They are written into ES-DE's scripts
folder below RetroDECK's home (``RetroDeckFolders.esde_folders``) at the
backend's start where the setting writes ES-DE, and whenever the setting is
stored; a folder that is refused, and a script that cannot be written or
removed, is logged and the rest go ahead.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from domain.esde_hook_scripts import hook_scripts, script_path
from domain.library_surface import library_surface, writes_esde
from domain.retrodeck_folders import FolderRefused

if TYPE_CHECKING:
    import asyncio
    import logging

    from domain.esde_hook_scripts import EsdeHookAddress
    from domain.library_surface import LibrarySurface
    from services.protocols import DebugLogger, EsdeHookScriptStore, RetroDeckFolders


@dataclass(frozen=True)
class EsdeHookScriptServiceConfig:
    """Frozen wiring bundle handed to ``EsdeHookScriptService.__init__``.

    ``store`` writes and removes the scripts, and ``retrodeck_folders`` answers
    ES-DE's scripts folder. ``address`` is where a script finds this backend,
    which the entry point decides. ``settings`` is the live dict the start reads
    the library surface from.
    """

    store: EsdeHookScriptStore
    retrodeck_folders: RetroDeckFolders
    address: EsdeHookAddress
    settings: dict[str, Any]
    loop: asyncio.AbstractEventLoop
    logger: logging.Logger
    log_debug: DebugLogger


class EsdeHookScriptService:
    """Writes and removes Tender's ES-DE event scripts; the ``LibrarySurfaceListener`` the settings tell."""

    def __init__(self, *, config: EsdeHookScriptServiceConfig) -> None:
        self._store = config.store
        self._retrodeck_folders = config.retrodeck_folders
        self._scripts = hook_scripts(config.address)
        self._settings = config.settings
        self._loop = config.loop
        self._logger = config.logger
        self._log_debug = config.log_debug

    def install_at_start(self) -> None:
        """Write the scripts once at the backend's start where the library surface writes ES-DE; else do nothing.

        Blocking, for a start-up step. Written at every start because each
        names this install's hook files, which the scripts read on every call.
        """
        if writes_esde(library_surface(self._settings.get("library_surface"))):
            self._install_io()

    async def surface_stored(self, surface: LibrarySurface) -> None:
        """Write the scripts once *surface* writes ES-DE, and remove Tender's once it does not."""
        await self._loop.run_in_executor(None, self._install_io if writes_esde(surface) else self._remove_io)

    def _install_io(self) -> int:
        folder = self._scripts_folder_io()
        if folder is None:
            return 0
        written = 0
        for script in self._scripts:
            path = script_path(folder, script.event)
            try:
                if self._store.install(path, script.text):
                    written += 1
                else:
                    self._logger.warning(f"[esde] {path} is not Tender's, so ES-DE's {script.event} calls no hook")
            except OSError as e:
                self._logger.warning(f"[esde] the {script.event} script was not written at {path}: {e!r}")
        self._log_debug(f"[esde] wrote {written} event scripts into {folder}")
        return written

    def _remove_io(self) -> int:
        folder = self._scripts_folder_io()
        if folder is None:
            return 0
        removed = 0
        for script in self._scripts:
            path = script_path(folder, script.event)
            try:
                removed += int(self._store.remove(path))
            except OSError as e:
                self._logger.warning(f"[esde] the {script.event} script at {path} was not removed: {e!r}")
        self._log_debug(f"[esde] removed {removed} event scripts from {folder}")
        return removed

    def _scripts_folder_io(self) -> str | None:
        folders = self._retrodeck_folders.esde_folders()
        if isinstance(folders, FolderRefused):
            self._log_debug(f"[esde] ES-DE's event scripts were left alone: {folders.message}")
            return None
        return folders.scripts
