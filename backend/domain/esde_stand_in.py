"""A stand-in: what Tender places in RetroDECK's ROM folder for a game that is not downloaded.

ES-DE lists only what lies in a system's folder, so a game that is not
downloaded is listed through a stand-in placed at exactly the path its download
would take: an absolute symlink to an empty stub under Tender's cache root,
``<cache>/esde/stand-ins/<rom_id>/<name>``. A link is Tender's stand-in exactly
when its target lies below that folder, and the target's ``<rom_id>`` component
names the game. ES-DE lists a link to an empty file, and a link to a folder
named like a file holding an empty file of the same name, as it lists the
download they stand for (docs/specs/esde-library.md, §2).

The entry a download leaves is predicted by the download's own functions, in
the order ``services/downloads/unpack.py`` runs them. Where that entry is one
ES-DE would not list as one game, there is no stand-in. A single zip RomM lists
no members for is predicted as the zip itself, the one prediction that may
miss (docs/specs/esde-library.md, D5).

The entry ES-DE lists a game as — its stand-in, or what its download left —
is what the game's media are named after (``domain/esde_media.py``), and is
read here too. The record of what Tender placed, ``<data>/esde/index.json``, is
encoded and decoded here as well.

Pure compute — no I/O, no state mutation.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

from domain.disc_formats import DISC_IMAGE_EXTENSIONS
from domain.disc_selection import one_descriptor_per_disc
from domain.rom_adoption import server_manifest
from domain.rom_files import (
    build_m3u_content,
    detect_launch_file,
    es_de_collapse_rename,
    folder_boot_layout_root,
    is_multi_file_download,
    m3u_playlist_name,
    needs_m3u,
    safe_extract_dir_name,
    safe_local_file_name,
)
from domain.served_shape import holds_disc_image, is_zip_name, listed_members, unpacked_manifest

if TYPE_CHECKING:
    from collections.abc import Collection, Mapping

    from domain.rom_install import RomInstall

_STAND_IN_FOLDER = ("esde", "stand-ins")

ShapeKind = Literal["file", "folder"]
FILE_SHAPE: Literal["file"] = "file"
FOLDER_SHAPE: Literal["folder"] = "folder"

# What placing a stand-in came to: placed, already there as it should be, or
# refused because something that is not this game's stand-in holds the path.
PlaceOutcome = Literal["placed", "kept", "occupied"]
PLACED: Literal["placed"] = "placed"
KEPT: Literal["kept"] = "kept"
OCCUPIED: Literal["occupied"] = "occupied"

_INDEX_VERSION = 1

# The system's folder a prediction lays the download out in: a path of its own,
# since nothing of the prediction depends on where the folder really is.
_FOLDER = os.sep


@dataclass(frozen=True, slots=True)
class StandInShape:
    """The entry a download of a game leaves in its system's folder, which the game's stand-in takes.

    *name* is the entry's own; *launch_name* is, for a folder, the file directly
    inside it that ES-DE launches, and ``None`` for a file.
    """

    name: str
    kind: ShapeKind
    launch_name: str | None = None


@dataclass(frozen=True, slots=True)
class StandInRecord:
    """What Tender decided for one game: where its stand-in goes and the shape it takes, or that it gets none.

    *updated_at* is RomM's for the ROM when the shape was predicted, so a ROM
    RomM has not changed since is never asked about again. *path* and *shape*
    are ``None`` together where the game gets no stand-in.
    """

    platform_slug: str
    system: str
    updated_at: str | None
    path: str | None
    shape: StandInShape | None


@dataclass(frozen=True, slots=True)
class SyncedScope:
    """What an apply run that was not cancelled synced, which decides the stand-ins it keeps.

    *representatives* are the ROMs it gives shortcuts, on the units it fetched
    and on those it skipped; *read_platforms* the platforms it fetched whole;
    *unchanged_platforms* the platforms it skipped as unchanged since their last
    apply; *unchanged_rom_ids* the members of the collections it skipped so.
    """

    representatives: frozenset[int]
    read_platforms: frozenset[str]
    unchanged_platforms: frozenset[str]
    unchanged_rom_ids: frozenset[int]

    def keeps(self, rom_id: int, platform_slug: str) -> bool:
        """Whether the game *rom_id*, on the platform *platform_slug*, is one the run still syncs.

        A representative is. So is a game on a platform skipped as unchanged,
        whose skip hands back only the ROMs bound to shortcuts, and a member of a
        collection skipped as unchanged on a platform the run did not read. A
        platform the run read named its representatives itself, so any other
        game recorded on it is a ROM RomM no longer lists or a version no longer
        its group's representative; any other game elsewhere is on a platform
        no longer synced.
        """
        if rom_id in self.representatives or platform_slug in self.unchanged_platforms:
            return True
        return rom_id in self.unchanged_rom_ids and platform_slug not in self.read_platforms


@dataclass(frozen=True, slots=True)
class EsdeEntry:
    """What ES-DE lists one game as, directly in its system's folder: its stand-in, or what its download left.

    *is_directory* says *path* is a folder ES-DE interprets as a file.
    """

    rom_id: int
    platform_slug: str
    system: str
    path: str
    is_directory: bool


@dataclass(frozen=True, slots=True)
class SettledRun:
    """What the end of an apply run that was not cancelled kept, and the entries a download holds that it kept.

    *entries* are those of the games it kept that have a stand-in recorded and
    are downloaded now, each as its download left it.
    """

    scope: SyncedScope
    entries: tuple[EsdeEntry, ...]


def wanted_links(kept: Mapping[int, StandInRecord], installed: Collection[int]) -> dict[str, int]:
    """Where a stand-in belongs, by path, with its game: each *kept* game's that is not *installed*."""
    return {
        record.path: rom_id for rom_id, record in kept.items() if record.path is not None and rom_id not in installed
    }


def stand_in_entry(rom_id: int, record: StandInRecord) -> EsdeEntry | None:
    """The entry *rom_id*'s stand-in, as *record* decides it, is listed as; ``None`` where it gets none."""
    if record.path is None or record.shape is None:
        return None
    return EsdeEntry(
        rom_id=rom_id,
        platform_slug=record.platform_slug,
        system=record.system,
        path=record.path,
        is_directory=record.shape.kind == FOLDER_SHAPE,
    )


def install_entry(install: RomInstall, folder: str) -> EsdeEntry | None:
    """The entry *install* left in its system's *folder* that ES-DE lists as one game; ``None`` where it left none.

    A single file directly in *folder* is listed as itself. A folder directly in
    *folder* is listed as one game only where the file it launches lies
    directly inside it under the folder's own name, ES-DE's directory
    interpreted as a file; any other folder ES-DE lists as a folder of games.
    """
    folder = os.path.normpath(folder)
    file_path = os.path.normpath(install.file_path)
    if install.rom_dir is None:
        path, is_directory = file_path, False
    else:
        path, is_directory = os.path.normpath(install.rom_dir), True
        if file_path != os.path.join(path, os.path.basename(path)):
            return None
    if os.path.dirname(path) != folder:
        return None
    return EsdeEntry(
        rom_id=install.rom_id,
        platform_slug=install.platform_slug,
        system=install.system,
        path=path,
        is_directory=is_directory,
    )


def stand_in_root(cache_dir: str) -> str:
    """The folder every stand-in's stub lies in, below the cache root *cache_dir*."""
    return os.path.join(cache_dir, *_STAND_IN_FOLDER)


def stand_in_rom_id(target: str, root: str) -> int | None:
    """The game a link whose target is *target* stands in for, or ``None`` where it is no stand-in below *root*.

    A stand-in's target is absolute and names a stub ``<root>/<rom_id>/<name>``;
    it is read as written, and ``..`` segments are resolved lexically first.
    """
    if not os.path.isabs(target):
        return None
    prefix = root.rstrip(os.sep) + os.sep
    normal = os.path.normpath(target)
    if not normal.startswith(prefix):
        return None
    parts = normal[len(prefix) :].split(os.sep)
    rom_id = parts[0]
    if len(parts) < 2 or not (rom_id.isascii() and rom_id.isdigit()) or str(int(rom_id)) != rom_id:
        return None
    return int(rom_id)


def listing_settles_shape(rom: dict[str, Any]) -> bool:
    """Whether RomM's listing alone predicts *rom*'s stand-in: one file at the ROM's root, and no zip.

    The listing the sync walks carries no ``files``
    (docs/adr/0028-adopted-install-is-an-install.md, "Storing the served shape
    on the ``roms`` row"), so every other shape — a nested single file, a
    multi-file ROM, a zip whose members decide — is predicted from RomM's detail.
    """
    return (
        rom.get("has_simple_single_file") is True
        and not rom.get("has_multiple_files")
        and not rom.get("has_nested_single_file")
        and not is_zip_name(safe_local_file_name(rom).name)
    )


def predict_stand_in(rom_detail: dict[str, Any], *, m3u_supported: bool) -> StandInShape | None:
    """The entry a download of *rom_detail* leaves in its system's folder, or ``None`` where it gets no stand-in.

    *m3u_supported* is whether ES-DE lists ``.m3u`` for the system, which the
    download asks before it writes a playlist or launches one. A single file is
    the name the download gives it. A multi-file ROM, and a single zip whose
    members RomM lists and which holds a disc image at its top level, is the
    folder it is unpacked into, renamed after its launch file. A single zip
    RomM lists no members for is the zip itself.
    """
    if is_multi_file_download(rom_detail):
        files = [(entry.rel_path or entry.name, entry.size_bytes) for entry in server_manifest(rom_detail)]
        return _unpacked(rom_detail, files, m3u_supported=m3u_supported)
    name = safe_local_file_name(rom_detail).name
    if not is_zip_name(name):
        return StandInShape(name=name, kind=FILE_SHAPE)
    members = listed_members(rom_detail)
    if members is None or not holds_disc_image(member.name for member in members):
        return StandInShape(name=name, kind=FILE_SHAPE)
    files = [(entry.rel_path, entry.size_bytes) for entry in unpacked_manifest(server_manifest(rom_detail))]
    return _unpacked(rom_detail, files, m3u_supported=m3u_supported)


def _unpacked(rom_detail: dict[str, Any], files: list[tuple[str, int]], *, m3u_supported: bool) -> StandInShape | None:
    """The entry *files*, unpacked as ``DownloadUnpacker.unpack_and_record`` unpacks them, leave — if ES-DE lists it.

    *files* are ``(path inside the folder, size)``. A file that would land
    outside the folder is one the extraction refuses, and so is the download.
    The PS3 heal is left out: it only ever runs on a folder-boot layout, whose
    launch file is nested and so listed as no one game whatever the heal adds.
    """
    folder = os.path.join(_FOLDER, safe_extract_dir_name(rom_detail).name)
    placed: list[tuple[str, int]] = []
    for rel, size in files:
        path = os.path.normpath(os.path.join(folder, rel))
        if not path.startswith(folder + os.sep):
            return None
        placed.append((path, size))
    paths = [path for path, _size in placed]
    # ``generate_m3u``: no playlist for a folder-boot layout or beside one RomM holds.
    if (
        m3u_supported
        and folder_boot_layout_root(paths) is None
        and not any(path.lower().endswith(".m3u") for path in paths)
    ):
        discs = one_descriptor_per_disc(
            [os.path.relpath(path, folder) for path in paths if path.lower().endswith(tuple(DISC_IMAGE_EXTENSIONS))]
        )
        if needs_m3u(discs, m3u_supported):
            playlist = os.path.join(folder, f"{m3u_playlist_name(rom_detail)}.m3u")
            placed.append((playlist, len(build_m3u_content(discs).encode())))
    placed.sort()
    launch = detect_launch_file(placed, m3u_supported)
    # The download reads the folder in whatever order the filesystem lists it,
    # so a launch file the order decides is one nothing here can predict.
    if launch is None or launch != detect_launch_file(placed[::-1], m3u_supported):
        return None
    renamed = es_de_collapse_rename(folder, launch)
    if renamed is not None:
        folder, launch = renamed
    name = os.path.basename(folder)
    if os.path.dirname(launch) != folder or os.path.basename(launch) != name:
        return None
    return StandInShape(name=name, kind=FOLDER_SHAPE, launch_name=name)


def encode_index(index: Mapping[int, StandInRecord]) -> str:
    """The record of what Tender placed, as ``<data>/esde/index.json`` holds it."""
    return json.dumps(
        {
            "version": _INDEX_VERSION,
            "games": {str(rom_id): _encode_record(record) for rom_id, record in sorted(index.items())},
        },
        indent=1,
    )


def _encode_record(record: StandInRecord) -> dict[str, Any]:
    shape = record.shape
    return {
        "platform_slug": record.platform_slug,
        "system": record.system,
        "updated_at": record.updated_at,
        "path": record.path,
        "shape": None if shape is None else {"name": shape.name, "kind": shape.kind, "launch_name": shape.launch_name},
    }


def decode_index(raw: str) -> dict[int, StandInRecord] | None:
    """The record *raw* holds, or ``None`` where it is not one this version writes."""
    try:
        data = json.loads(raw)
    except ValueError:
        return None
    if not isinstance(data, dict) or data.get("version") != _INDEX_VERSION or not isinstance(data.get("games"), dict):
        return None
    index: dict[int, StandInRecord] = {}
    for key, value in data["games"].items():
        record = _decode_record(value)
        if not (key.isascii() and key.isdigit()) or record is None:
            return None
        index[int(key)] = record
    return index


def _decode_record(value: object) -> StandInRecord | None:
    if not isinstance(value, dict):
        return None
    platform_slug, system = value.get("platform_slug"), value.get("system")
    updated_at, path = value.get("updated_at"), value.get("path")
    if not (isinstance(platform_slug, str) and isinstance(system, str)):
        return None
    if not (updated_at is None or isinstance(updated_at, str)) or not (path is None or isinstance(path, str)):
        return None
    shape = _decode_shape(value.get("shape"))
    if (path is None) != (value.get("shape") is None) or (path is not None and shape is None):
        return None
    return StandInRecord(platform_slug=platform_slug, system=system, updated_at=updated_at, path=path, shape=shape)


def _decode_shape(value: object) -> StandInShape | None:
    if not isinstance(value, dict):
        return None
    name, kind, launch_name = value.get("name"), value.get("kind"), value.get("launch_name")
    if not isinstance(name, str) or not (launch_name is None or isinstance(launch_name, str)):
        return None
    if kind == FILE_SHAPE:
        return StandInShape(name=name, kind=FILE_SHAPE, launch_name=launch_name)
    if kind == FOLDER_SHAPE:
        return StandInShape(name=name, kind=FOLDER_SHAPE, launch_name=launch_name)
    return None
