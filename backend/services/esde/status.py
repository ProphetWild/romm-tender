"""What the ES-DE settings section shows: the surface, Tender's event scripts, ES-DE's last call, and RomM's media.

Each part is read where it lives — the scripts through the script service, the
last call off the hook service, the media types off RomM's server config — and a
part that could not be read says so rather than reading as empty.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from domain.esde_scan_media import folders_not_collected
from domain.library_surface import library_surface
from domain.retrodeck_folders import FolderRefused
from lib.errors import RommApiError, classify_error

if TYPE_CHECKING:
    import asyncio
    import logging
    from collections.abc import Awaitable, Callable

    from services.esde.hook_scripts import HookScriptsReading
    from services.esde.hooks import EsdeHookCall
    from services.protocols import RommServerConfigReader


@dataclass(frozen=True)
class EsdeStatusServiceConfig:
    """Frozen wiring bundle handed to ``EsdeStatusService.__init__``.

    ``read_scripts`` answers how many of Tender's scripts are in ES-DE's scripts
    folder, ``last_call`` the last call ES-DE made since this backend started,
    and ``romm_config`` what RomM's server collects. ``settings`` is the live
    dict the surface is read from.
    """

    settings: dict[str, Any]
    read_scripts: Callable[[], Awaitable[HookScriptsReading | FolderRefused]]
    last_call: Callable[[], EsdeHookCall | None]
    romm_config: RommServerConfigReader
    loop: asyncio.AbstractEventLoop
    logger: logging.Logger


class EsdeStatusService:
    """Answers the ES-DE settings section's read."""

    def __init__(self, *, config: EsdeStatusServiceConfig) -> None:
        self._settings = config.settings
        self._read_scripts = config.read_scripts
        self._last_call = config.last_call
        self._romm_config = config.romm_config
        self._loop = config.loop
        self._logger = config.logger

    async def get_esde_status(self) -> dict[str, Any]:
        """The library surface, the scripts, ES-DE's last call and the ES-DE media folders RomM does not collect.

        ``scripts`` is ``known`` with the folder and how many of the ``expected``
        scripts are ``installed`` there, or not ``known`` with the refusal's
        reason and message. ``last_call`` is ``None`` before ES-DE's first call.
        ``media_not_collected`` is ``established`` with the ``folders`` RomM's
        ``scan.media`` leaves without media, in ES-DE's folder order, or not
        ``established`` with a message where RomM's config could not be read:
        an unread config never reads as nothing missing.
        """
        last = self._last_call()
        return {
            "surface": library_surface(self._settings.get("library_surface")),
            "scripts": _scripts_answer(await self._read_scripts()),
            "last_call": None if last is None else {"hook": last.hook, "at": last.at},
            "media_not_collected": await self._media_not_collected(),
        }

    async def _media_not_collected(self) -> dict[str, Any]:
        try:
            scan_media = await self._loop.run_in_executor(None, self._romm_config.get_scan_media)
        except RommApiError as e:
            self._logger.info(f"[esde] RomM's scan.media could not be read: {e!r}")
            return {"established": False, "message": classify_error(e)[1]}
        return {"established": True, "folders": list(folders_not_collected(scan_media))}


def _scripts_answer(reading: HookScriptsReading | FolderRefused) -> dict[str, Any]:
    if isinstance(reading, FolderRefused):
        return {"known": False, "reason": reading.reason, "message": reading.message}
    return {"known": True, "folder": reading.folder, "installed": reading.installed, "expected": reading.expected}
