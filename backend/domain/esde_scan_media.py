"""Which of ES-DE's media folders RomM's server is not set to collect, read off its ``scan.media`` setting.

RomM stores a media type only where its server config lists it under
``scan.media`` (``config_manager.py``), and a type it does not store is one
Tender has nothing to fetch for; which value fills which folder is
docs/specs/esde-library.md, section 3a. RomM always stores the cover, so the
``covers`` folder is never one that is missing.

Pure compute — no I/O.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from domain.esde_media import MEDIA_FOLDERS

if TYPE_CHECKING:
    from collections.abc import Iterable

    from domain.esde_media import MediaFolder

# Any one of a folder's values is enough for RomM to store what fills it.
SCAN_MEDIA_FOR_FOLDER: dict[MediaFolder, tuple[str, ...]] = {
    "marquees": ("logo",),
    "screenshots": ("screenshot",),
    "titlescreens": ("title_screen",),
    "3dboxes": ("box3d",),
    "backcovers": ("box2d_back",),
    "physicalmedia": ("physical",),
    "fanart": ("fanart",),
    "miximages": ("miximage",),
    "videos": ("video", "video_normalized"),
    "manuals": ("manual",),
}


def folders_not_collected(scan_media: Iterable[str]) -> tuple[MediaFolder, ...]:
    """The folders, in ES-DE's folder order, none of whose values *scan_media* lists."""
    collected = set(scan_media)
    return tuple(
        folder
        for folder in MEDIA_FOLDERS
        if folder in SCAN_MEDIA_FOR_FOLDER and collected.isdisjoint(SCAN_MEDIA_FOR_FOLDER[folder])
    )
