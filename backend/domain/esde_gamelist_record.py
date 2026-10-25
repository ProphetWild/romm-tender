"""Where a game's entry stands in ES-DE's gamelists, and the record of what Tender is to write there and last wrote.

Contract: the ``<path>`` ES-DE writes for a game listed directly in its
system's folder, and where that system's gamelist lies; and, per system, the
record ``<data>/esde/gamelists.json`` holds, encoded and decoded here — the
entries waiting to be written, each with the fields it is to carry, and what
Tender last wrote, exactly as ``domain.esde_gamelist.merge_gamelist`` answered
it. How the fields are merged into the file is that module's.

Pure compute — no I/O, no state mutation.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

from domain.esde_gamelist import TENDER_FIELDS

if TYPE_CHECKING:
    from collections.abc import Mapping

_GAMELIST_FILE = "gamelist.xml"
_INDEX_VERSION = 1

# Everything outside XML 1.0's ``Char`` production: a name holding one is a
# path no gamelist can carry, and the merge writes a path as it is.
_UNWRITABLE = re.compile("[^\t\n\r\x20-퟿-�\U00010000-\U0010ffff]")


@dataclass(frozen=True, slots=True)
class SystemGamelist:
    """One system's gamelist as Tender keeps it: the entries waiting to be written, and what Tender last wrote.

    Both map an entry's ``<path>`` to its fields. *pending* holds the values
    each is to carry, *written* the record ``merge_gamelist`` answered when
    the file was last written.
    """

    pending: Mapping[str, Mapping[str, str]]
    written: Mapping[str, Mapping[str, str]]


def gamelist_entry_path(entry_name: str) -> str | None:
    """The ``<path>`` ES-DE writes for the game listed as *entry_name* in its system's folder; else ``None``.

    ES-DE writes ``./`` and the entry's name (``createRelativePath`` in
    ``GamelistFileParser::addFileDataNode``), a directory interpreted as a file
    included, whose game is the directory itself (``SystemData::populateFolder``).
    ``None`` for a name ES-DE reads back as no one entry of the folder — empty,
    ``.``, ``..``, or holding a slash or a backslash, which ES-DE reads as a
    slash (``getGenericPath``) — and for one holding a character no XML file
    can carry.
    """
    if not is_path_component(entry_name) or "\\" in entry_name or _UNWRITABLE.search(entry_name):
        return None
    return f"./{entry_name}"


def gamelist_file(gamelists_root: str, system: str) -> str:
    """Where ES-DE reads *system*'s gamelist below its *gamelists_root* (``SystemData::getGamelistPath``).

    Raises ``ValueError`` for a system that is not one path component.
    """
    if not is_path_component(system):
        raise ValueError(f"{system!r} is not a single path component")
    return os.path.join(gamelists_root, system, _GAMELIST_FILE)


def encode_gamelist_index(index: Mapping[str, SystemGamelist]) -> str:
    """The record of every system's gamelist, as ``<data>/esde/gamelists.json`` holds it."""
    return json.dumps(
        {
            "version": _INDEX_VERSION,
            "systems": {
                system: {"pending": _plain(gamelist.pending), "written": _plain(gamelist.written)}
                for system, gamelist in sorted(index.items())
            },
        },
        indent=1,
    )


def decode_gamelist_index(raw: str) -> dict[str, SystemGamelist] | None:
    """The record *raw* holds, or ``None`` where it is not one this version writes."""
    try:
        data = json.loads(raw)
    except ValueError:
        return None
    if not isinstance(data, dict) or data.get("version") != _INDEX_VERSION or not isinstance(data.get("systems"), dict):
        return None
    index: dict[str, SystemGamelist] = {}
    for system, value in data["systems"].items():
        if not is_path_component(system) or not isinstance(value, dict):
            return None
        pending, written = _decode_entries(value.get("pending")), _decode_entries(value.get("written"))
        if pending is None or written is None:
            return None
        index[system] = SystemGamelist(pending=pending, written=written)
    return index


def _decode_entries(value: object) -> dict[str, dict[str, str]] | None:
    if not isinstance(value, dict):
        return None
    entries: dict[str, dict[str, str]] = {}
    for path, fields in value.items():
        if path != gamelist_entry_path(path.removeprefix("./")) or not isinstance(fields, dict):
            return None
        if not set(fields) <= set(TENDER_FIELDS) or not all(isinstance(text, str) for text in fields.values()):
            return None
        entries[path] = dict(fields)
    return entries


def _plain(entries: Mapping[str, Mapping[str, str]]) -> dict[str, dict[str, str]]:
    return {path: dict(fields) for path, fields in sorted(entries.items())}


def is_path_component(name: str) -> bool:
    """Whether *name* is one component of a path: a system's gamelist folder must be."""
    return name not in ("", ".", "..") and "/" not in name and "\0" not in name
