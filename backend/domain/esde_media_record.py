"""The record of the media Tender linked into ES-DE, and the rules a fetch and the on-demand cap read it by.

Contract: per game, the entry stem and system its media are named under and,
per media folder, the RomM file it is filled from, the copy Tender keeps of it
and where that copy's link stands — the vocabulary ``<data>/esde/media.json``
holds, encoded and decoded here. Beside it, the pure decisions taken against
the record: whether a kept copy is still current, when a fetch may revalidate
instead of downloading again, and which on-demand copies the cache's cap
evicts. What the folders and names are is ``domain/esde_media.py``'s.

Pure compute — no I/O, no state mutation.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any, Literal

from domain.cover_refresh import cover_ts_only_change
from domain.esde_media import MEDIA_FOLDERS, ON_SELECTION_FOLDERS, CachedMedia, MediaFolder, evictions

if TYPE_CHECKING:
    from collections.abc import Mapping

# What linking a kept copy came to: a link made or pointed anew, the link
# already there as it should be, or nothing linked because a file that is not
# Tender's holds one of the names ES-DE reads for that game and folder.
LinkOutcome = Literal["linked", "unchanged", "held"]
LINKED: Literal["linked"] = "linked"
UNCHANGED: Literal["unchanged"] = "unchanged"
HELD: Literal["held"] = "held"

_INDEX_VERSION = 1


@dataclass(frozen=True, slots=True)
class FolderMedia:
    """One media folder of one game: the RomM file it is filled from, and what Tender keeps and links of it.

    *fingerprint* is ``media_fingerprint`` of the copy last fetched, ``None``
    before one was; *etag* and *last_modified* are the validators that answer
    carried. *extension* is the kept copy's, ``None`` where nothing is kept —
    RomM served no file there, or bytes ES-DE does not read. *link_path* is
    where Tender's link to it stands, ``None`` where none does. For a folder
    fetched on selection, *last_selected* is when its game was last selected
    and *size_bytes* the kept copy's size.
    """

    server_path: str
    fingerprint: str | None
    etag: str | None
    last_modified: str | None
    extension: str | None
    link_path: str | None
    last_selected: float | None = None
    size_bytes: int | None = None


@dataclass(frozen=True, slots=True)
class GameMedia:
    """The media Tender keeps for one game, named after the entry ES-DE lists it as.

    *updated_at* is RomM's for the ROM when its sources were last read, which
    a folder fetched on selection is fingerprinted with.
    """

    platform_slug: str
    system: str
    stem: str
    updated_at: str | None
    folders: Mapping[MediaFolder, FolderMedia]


def is_current(media: FolderMedia | None, fingerprint: str, *, cached: bool) -> bool:
    """Whether *media* settles a folder whose file is *fingerprint* now, so nothing is fetched for it.

    *cached* says the copy *media* names is still kept. A file RomM did not
    serve, or whose bytes ES-DE does not read, keeps no copy and stays settled
    until its fingerprint moves.
    """
    if media is None or media.fingerprint != fingerprint:
        return False
    return media.extension is None or cached


def revalidates(media: FolderMedia | None, server_path: str, *, cached: bool) -> bool:
    """Whether a fetch of *server_path* may send *media*'s validators, so a 304 keeps the copy.

    Only for the same file on the server — RomM moves a cover's ``?ts=`` on a
    rescan that leaves the file alone (``domain/cover_refresh.py``) — and only
    while the copy those validators describe is still kept.
    """
    if media is None or media.extension is None or not cached or not (media.etag or media.last_modified):
        return False
    return _same_file(media.server_path, server_path)


def with_source(media: FolderMedia | None, server_path: str) -> FolderMedia:
    """*media* filled from *server_path* from now on, its kept copy left as it is until a selection asks for it.

    Its validators go where *server_path* is another file than the one they
    describe.
    """
    if media is None:
        return FolderMedia(
            server_path=server_path, fingerprint=None, etag=None, last_modified=None, extension=None, link_path=None
        )
    if _same_file(media.server_path, server_path):
        return replace(media, server_path=server_path)
    return replace(media, server_path=server_path, etag=None, last_modified=None)


def on_demand_evictions(index: Mapping[int, GameMedia], cap_bytes: int) -> list[tuple[int, MediaFolder]]:
    """The copies fetched on selection to evict so that what stays fits in *cap_bytes*, in the order they go.

    Each is ``(rom_id, folder)``. A copy whose game was never selected counts
    as selected longest ago.
    """
    owners: dict[str, tuple[int, MediaFolder]] = {}
    entries: list[CachedMedia] = []
    for rom_id, game in index.items():
        for folder, media in game.folders.items():
            if folder not in ON_SELECTION_FOLDERS or media.extension is None or media.size_bytes is None:
                continue
            key = f"{rom_id}/{folder}"
            owners[key] = (rom_id, folder)
            entries.append(CachedMedia(key, media.size_bytes, media.last_selected or 0.0))
    return [owners[key] for key in evictions(entries, cap_bytes)]


def encode_media_index(index: Mapping[int, GameMedia]) -> str:
    """The record of the media Tender linked, as ``<data>/esde/media.json`` holds it."""
    return json.dumps(
        {
            "version": _INDEX_VERSION,
            "games": {str(rom_id): _encode_game(game) for rom_id, game in sorted(index.items())},
        },
        indent=1,
        ensure_ascii=False,
    )


def decode_media_index(raw: str) -> dict[int, GameMedia] | None:
    """The record *raw* holds, or ``None`` where it is not one this version writes."""
    try:
        data = json.loads(raw)
    except ValueError:
        return None
    if not isinstance(data, dict) or data.get("version") != _INDEX_VERSION or not isinstance(data.get("games"), dict):
        return None
    index: dict[int, GameMedia] = {}
    for key, value in data["games"].items():
        game = _decode_game(value)
        if not (key.isascii() and key.isdigit()) or game is None:
            return None
        index[int(key)] = game
    return index


def _same_file(recorded: str, server_path: str) -> bool:
    return recorded == server_path or cover_ts_only_change(recorded, server_path)


def _encode_game(game: GameMedia) -> dict[str, Any]:
    return {
        "platform_slug": game.platform_slug,
        "system": game.system,
        "stem": game.stem,
        "updated_at": game.updated_at,
        "folders": {folder: _encode_folder(media) for folder, media in game.folders.items()},
    }


def _encode_folder(media: FolderMedia) -> dict[str, Any]:
    return {
        "server_path": media.server_path,
        "fingerprint": media.fingerprint,
        "etag": media.etag,
        "last_modified": media.last_modified,
        "extension": media.extension,
        "link_path": media.link_path,
        "last_selected": media.last_selected,
        "size_bytes": media.size_bytes,
    }


def _decode_game(value: object) -> GameMedia | None:
    if not isinstance(value, dict):
        return None
    platform_slug, system, stem, updated_at = (value.get(k) for k in ("platform_slug", "system", "stem", "updated_at"))
    if not (isinstance(platform_slug, str) and isinstance(system, str) and isinstance(stem, str)):
        return None
    folders = value.get("folders")
    if not _optional(updated_at, str) or not isinstance(folders, dict):
        return None
    decoded: dict[MediaFolder, FolderMedia] = {}
    for folder in MEDIA_FOLDERS:
        if folder in folders:
            media = _decode_folder(folders[folder])
            if media is None:
                return None
            decoded[folder] = media
    if len(decoded) != len(folders):
        return None
    return GameMedia(platform_slug=platform_slug, system=system, stem=stem, updated_at=updated_at, folders=decoded)


def _decode_folder(value: object) -> FolderMedia | None:
    if not isinstance(value, dict) or not isinstance(value.get("server_path"), str):
        return None
    texts = [value.get(k) for k in ("fingerprint", "etag", "last_modified", "extension", "link_path")]
    last_selected, size_bytes = value.get("last_selected"), value.get("size_bytes")
    if not all(_optional(text, str) for text in texts):
        return None
    if not _optional(last_selected, float | int) or isinstance(last_selected, bool):
        return None
    if not _optional(size_bytes, int) or isinstance(size_bytes, bool):
        return None
    fingerprint, etag, last_modified, extension, link_path = texts
    return FolderMedia(
        server_path=value["server_path"],
        fingerprint=fingerprint,
        etag=etag,
        last_modified=last_modified,
        extension=extension,
        link_path=link_path,
        last_selected=None if last_selected is None else float(last_selected),
        size_bytes=size_bytes,
    )


def _optional(value: object, kind: Any) -> bool:
    return value is None or isinstance(value, kind)
