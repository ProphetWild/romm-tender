"""A RomM ROM's values for the fields Tender writes into an ES-DE gamelist.

Contract: the one reading of a ROM into ``domain.esde_gamelist.TENDER_FIELDS``,
from ``domain.rom_metadata_mapping``'s reading of its metadata, in the form ES-DE
stores each field. A field the ROM has no value for is left out. Pure compute.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from domain.rom_metadata_mapping import build_rom_metadata

# ES-DE's default release date (``GAME_METADATA``, es-app/src/MetaData.cpp):
# it reads this value as no date and leaves it out of every entry it writes.
_ESDE_NO_DATE = "19700101T000000"
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


def gamelist_fields(rom: dict[str, Any]) -> dict[str, str]:
    """Answer the gamelist fields for the RomM ROM dict *rom* (an ``/api/roms`` item).

    ``name`` is the ROM's name, ``desc`` its summary, ``releasedate`` its first
    release day in UTC as ES-DE's ``YYYYMMDDT000000``, ``genre`` its genres
    joined with ", ", ``developer`` and ``publisher`` its first and second
    company, ``players`` its player count. Values are trimmed, and an empty one
    is left out. A ROM whose metadata cannot be read answers its name alone.
    """
    fields: dict[str, str] = {}
    name = _text(rom.get("name"))
    if name:
        fields["name"] = name
    try:
        metadata = build_rom_metadata(rom, cached_at=0.0)
    except (ValueError, TypeError):
        return fields
    companies = _names(metadata.companies)
    values = {
        "desc": _text(metadata.summary),
        "releasedate": _release_date(metadata.first_release_date),
        "developer": companies[0] if companies else "",
        "publisher": companies[1] if len(companies) > 1 else "",
        "genre": ", ".join(_names(metadata.genres)),
        "players": _player_count(metadata.player_count),
    }
    fields.update({field: value for field, value in values.items() if value})
    return fields


def _release_date(seconds: int | None) -> str:
    if seconds is None:
        return ""
    try:
        day = _EPOCH + timedelta(seconds=seconds)
    except OverflowError:
        return ""
    # Formatted by hand: glibc's ``strftime("%Y")`` does not pad a year below 1000.
    date = f"{day.year:04d}{day.month:02d}{day.day:02d}T000000"
    return "" if date == _ESDE_NO_DATE else date


def _player_count(value: object) -> str:
    if isinstance(value, bool):
        return ""
    if isinstance(value, int):
        return str(value)
    return _text(value)


def _names(values: tuple[Any, ...]) -> list[str]:
    return [name for value in values if (name := _text(value))]


def _text(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""
