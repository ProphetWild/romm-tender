"""The library surface: where a library sync puts the RomM library — Steam's shortcuts, ES-DE, or both.

The vocabulary of the ``library_surface`` setting and the questions the rest of
the program asks of a surface. A stored value that names no surface reads as the
default, so a setting this module cannot read never turns Steam's shortcuts off.
"""

from __future__ import annotations

from typing import Literal

LibrarySurface = Literal["steam", "esde", "both"]

LIBRARY_SURFACES: tuple[LibrarySurface, ...] = ("steam", "esde", "both")
DEFAULT_LIBRARY_SURFACE: LibrarySurface = "steam"


def library_surface(value: object) -> LibrarySurface:
    """The surface a stored *value* names, or :data:`DEFAULT_LIBRARY_SURFACE` where it names none."""
    return value if value in LIBRARY_SURFACES else DEFAULT_LIBRARY_SURFACE


def creates_steam_shortcuts(surface: LibrarySurface) -> bool:
    """Whether a sync to *surface* creates Steam shortcuts."""
    return surface in ("steam", "both")


def writes_esde(surface: LibrarySurface) -> bool:
    """Whether a sync to *surface* writes the library into ES-DE."""
    return surface in ("esde", "both")
