"""Which RomM file fills which of ES-DE's media folders, and what ES-DE must find it under.

Contract: ES-DE's media vocabulary as Tender fills it — the folders, the
extensions ES-DE looks for in each and in which order, the name a game's file
takes there — and the rules that decide, from a RomM ROM and a file's first
bytes, what is fetched for each folder and what it is called; with the
fingerprint that says a fetched file is still current, and which files leave
the capped on-demand cache. Pure: fetching, sniffing a real file and linking it
are the caller's.
"""

from __future__ import annotations

import json
import posixpath
import re
from typing import TYPE_CHECKING, Any, Literal, NamedTuple

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

type MediaFolder = Literal[
    "covers",
    "marquees",
    "screenshots",
    "titlescreens",
    "3dboxes",
    "backcovers",
    "physicalmedia",
    "fanart",
    "miximages",
    "videos",
    "manuals",
]

SYNC_FOLDERS: tuple[MediaFolder, ...] = (
    "covers",
    "marquees",
    "screenshots",
    "titlescreens",
    "3dboxes",
    "backcovers",
    "physicalmedia",
    "fanart",
    "miximages",
)
ON_SELECTION_FOLDERS: tuple[MediaFolder, ...] = ("videos", "manuals")
MEDIA_FOLDERS: tuple[MediaFolder, ...] = SYNC_FOLDERS + ON_SELECTION_FOLDERS

# ES-DE takes the first of these that exists (``sImageExtensions``,
# ``sVideoExtensions``, es-app/src/FileData.h; ``FileData::getManualPath``), so
# a file under an earlier one shadows a later one.
IMAGE_LOOKUP_ORDER: tuple[str, ...] = (".png", ".jpg", ".webp")
VIDEO_LOOKUP_ORDER: tuple[str, ...] = (".mp4", ".mkv", ".avi", ".wmv", ".mov", ".webm", ".m4v")
MANUAL_LOOKUP_ORDER: tuple[str, ...] = (".pdf",)

# The bytes ``sniff_extension`` needs from the start of a file.
SNIFF_LENGTH = 64

_VIDEO_EXTENSIONS: tuple[str, ...] = (".mp4", ".mkv", ".webm")

# RomM's ``FRONTEND_RESOURCES_PATH`` (backend/config/__init__.py), which its
# absolute media paths start with and its relative ones are stored under.
_RESOURCES = "/assets/romm/resources/"
# The order RomM's own ES-DE exporter reads a ``*_path`` in (``_collect_assets``,
# backend/utils/gamelist_exporter.py).
_METADATA_BLOCKS = ("ss_metadata", "gamelist_metadata", "launchbox_metadata")
_BLOCK_PATHS: dict[MediaFolder, str] = {
    "titlescreens": "title_screen_path",
    "3dboxes": "box3d_path",
    "backcovers": "box2d_back_path",
    "physicalmedia": "physical_path",
    "fanart": "fanart_path",
    "miximages": "miximage_path",
}
_URI_SCHEME = re.compile(r"[A-Za-z][A-Za-z0-9+.\-]*:")

# The major brands of an ISO base media file (ISO/IEC 14496-12) read as MP4:
# ``isom``, ``iso2``…, ``mp41``, ``mp42``, ``avc1`` and Apple's ``M4V``. HEIF and
# AVIF images (``heic``, ``mif1``, ``avif``) open with the same box and are not.
_MP4_BRANDS = (b"iso", b"mp4", b"M4V", b"avc1")
_EBML_MAGIC = b"\x1a\x45\xdf\xa3"
_EBML_DOCTYPE = b"\x42\x82"


class CachedMedia(NamedTuple):
    """A file in the on-demand cache: its key, its size, and when its game was last selected."""

    key: str
    size_bytes: int
    last_selected: float


def media_sources(rom: Mapping[str, Any]) -> dict[MediaFolder, str]:
    """Answer the RomM server path to fetch for each media folder the RomM ROM dict *rom* has a file for.

    ``covers`` is ``path_cover_large``, else ``path_cover_small``, its query
    kept; ``marquees`` the wheel logo, ``logo_path``, else the gamelist's
    ``marquee_path`` (ScreenScraper's own ``marquee_path`` is a screen marquee,
    not ES-DE's); ``screenshots`` the first ``merged_screenshots`` entry in
    the ROM's own ``screenshots/`` folder, else the first; ``videos`` and
    ``manuals`` the ROM's ``path_video`` and ``path_manual``; each other folder
    its ``*_path``. A ``*_path`` is read from ``ss_metadata``, then
    ``gamelist_metadata``, then ``launchbox_metadata``, and the first that is a
    non-empty string is the folder's. A relative path is under RomM's resources;
    a URL or a path climbing with ``..`` is never answered, nor is a ``*_url``.
    """
    candidates: dict[MediaFolder, list[object]] = {
        "covers": [rom.get("path_cover_large"), rom.get("path_cover_small")],
        "marquees": [*_from_blocks(rom, "logo_path"), _block(rom, "gamelist_metadata").get("marquee_path")],
        "videos": [rom.get("path_video")],
        "manuals": [rom.get("path_manual")],
    }
    for folder, key in _BLOCK_PATHS.items():
        candidates[folder] = _from_blocks(rom, key)
    sources: dict[MediaFolder, str] = {}
    for folder in MEDIA_FOLDERS:
        path = _screenshot(rom) if folder == "screenshots" else _first_path(candidates[folder])
        if path is not None:
            sources[folder] = path
    return sources


def sniff_extension(head: bytes) -> str | None:
    """Answer the extension the file starting with *head* is, by its bytes, or ``None`` for one Tender does not write.

    *head* is at least the first ``SNIFF_LENGTH`` bytes, or the whole file.
    """
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if head.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return ".webp"
    if head[4:8] == b"ftyp" and head[8:12].startswith(_MP4_BRANDS):
        return ".mp4"
    if head.startswith(_EBML_MAGIC):
        return ".webm" if _ebml_doctype(head) == b"webm" else ".mkv"
    if head.startswith(b"%PDF-"):
        return ".pdf"
    return None


def lookup_order(folder: MediaFolder) -> tuple[str, ...]:
    """Answer the extensions ES-DE looks for in *folder*, in the order it looks."""
    if folder == "videos":
        return VIDEO_LOOKUP_ORDER
    if folder == "manuals":
        return MANUAL_LOOKUP_ORDER
    _check_folder(folder)
    return IMAGE_LOOKUP_ORDER


def accepted_extensions(folder: MediaFolder) -> tuple[str, ...]:
    """Answer the extensions Tender writes into *folder*."""
    if folder == "videos":
        return _VIDEO_EXTENSIONS
    return lookup_order(folder)


def extension_for(folder: MediaFolder, head: bytes) -> str | None:
    """Answer the extension the file starting with *head* takes in *folder*, or ``None`` where it goes nowhere there."""
    extension = sniff_extension(head)
    return extension if extension in accepted_extensions(folder) else None


def media_stem(entry_name: str, *, is_directory: bool) -> str:
    """Answer the stem ES-DE names a game's media by, for the entry *entry_name* in its system folder.

    ``Utils::FileSystem::getStem`` (es-core/src/utils/FileSystemUtil.cpp): a
    file's name less its last extension, and a directory's whole name, so
    ``Final Fantasy VII.m3u/`` keeps its ``.m3u``.
    """
    if not entry_name or "/" in entry_name:
        raise ValueError(f"{entry_name!r} is not an entry name")
    if is_directory:
        return entry_name
    dot = entry_name.rfind(".")
    return entry_name if dot == -1 else entry_name[:dot]


def media_link_path(media_root: str, system: str, folder: MediaFolder, stem: str, extension: str) -> str:
    """Answer where ES-DE looks for the *folder* file of the game whose media stem is *stem*.

    *media_root* is ES-DE's ``downloaded_media``. Raises ``ValueError`` for a
    system or stem that is not one path component, or an extension ES-DE does
    not look for in *folder*.
    """
    for component in (system, stem):
        if component in ("", ".", "..") or "/" in component or "\0" in component:
            raise ValueError(f"{component!r} is not a single path component")
    if extension not in lookup_order(folder):
        raise ValueError(f"ES-DE does not look for {extension!r} in {folder}")
    return posixpath.join(media_root, system, folder, stem + extension)


def media_fingerprint(server_path: str, updated_at: str | None) -> str:
    """Answer a fetched file's fingerprint: equal exactly while its server path and its ROM's ``updated_at`` are."""
    return json.dumps([server_path, updated_at], ensure_ascii=False)


def evictions(entries: Iterable[CachedMedia], cap_bytes: int) -> list[str]:
    """Answer the keys to evict so that what stays fits in *cap_bytes*, in the order they go.

    A file larger than the cap goes first, since it can never fit; then the
    least recently selected, a tie broken by key, until the rest fits. Raises
    ``ValueError`` for a negative size or cap.
    """
    if cap_bytes < 0:
        raise ValueError(f"a cap of {cap_bytes} bytes")
    ordered = sorted(entries, key=lambda entry: (entry.last_selected, entry.key))
    if any(entry.size_bytes < 0 for entry in ordered):
        raise ValueError("a cached file of negative size")
    evicted = [entry.key for entry in ordered if entry.size_bytes > cap_bytes]
    kept = [entry for entry in ordered if entry.size_bytes <= cap_bytes]
    total = sum(entry.size_bytes for entry in kept)
    for entry in kept:
        if total <= cap_bytes:
            break
        evicted.append(entry.key)
        total -= entry.size_bytes
    return evicted


def _check_folder(folder: str) -> None:
    if folder not in SYNC_FOLDERS:
        raise ValueError(f"{folder!r} is not a media folder Tender fills")


def _block(rom: Mapping[str, Any], name: str) -> Mapping[str, Any]:
    block = rom.get(name)
    return block if isinstance(block, dict) else {}


def _from_blocks(rom: Mapping[str, Any], key: str) -> list[object]:
    return [_block(rom, name).get(key) for name in _METADATA_BLOCKS]


def _first_path(candidates: Iterable[object]) -> str | None:
    for candidate in candidates:
        if isinstance(candidate, str) and candidate.strip():
            return _server_path(candidate)
    return None


def _server_path(value: str) -> str | None:
    if _URI_SCHEME.match(value) or "://" in value or ".." in value:
        return None
    return value if value.startswith("/") else _RESOURCES + value


def _screenshot(rom: Mapping[str, Any]) -> str | None:
    shots = rom.get("merged_screenshots")
    if not isinstance(shots, list):
        return None
    paths = [_server_path(shot) for shot in shots if isinstance(shot, str) and shot.strip()]
    if not paths:
        return None
    rom_id, platform_id = rom.get("id"), rom.get("platform_id")
    if _is_id(rom_id) and _is_id(platform_id):
        own = f"{_RESOURCES}roms/{platform_id}/{rom_id}/screenshots/"
        for path in paths:
            if path is not None and path.startswith(own):
                return path
    return paths[0]


def _is_id(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _ebml_doctype(head: bytes) -> bytes | None:
    """The DocType of the EBML header *head* starts with (RFC 8794), or ``None`` where *head* ends before it."""
    header = _vint(head, len(_EBML_MAGIC))
    if header is None:
        return None
    position = len(_EBML_MAGIC) + header[0]
    end = min(len(head), position + header[1])
    while position < end:
        element = _vint(head, position)
        if element is None:
            return None
        element_id = head[position : position + element[0]]
        position += element[0]
        size = _vint(head, position)
        if size is None:
            return None
        position += size[0]
        if element_id == _EBML_DOCTYPE:
            return head[position : position + size[1]] if position + size[1] <= len(head) else None
        position += size[1]
    return None


def _vint(data: bytes, position: int) -> tuple[int, int] | None:
    """The width and value of the EBML variable-length integer at *position*, or ``None`` where none can be read."""
    if position >= len(data) or data[position] == 0:
        return None
    first = data[position]
    width = 9 - first.bit_length()
    if position + width > len(data):
        return None
    value = first & (0xFF >> width)
    for byte in data[position + 1 : position + width]:
        value = (value << 8) | byte
    return width, value
