"""ES-DE's own folders in RetroDECK — its gamelists, its scripts and its downloaded media — below RetroDECK's home.

ES-DE as RetroDECK ships it keeps all three under ``<home>/ES-DE/``, the home
being the one RetroDECK's settings name (``rd_home_path``); where that was read
is docs/specs/esde-library.md, "What 0.33.0 already gives us". The vendored
resolver answers the home and none of the folders below it, so they are joined
onto the home here. Which home may be joined onto is
:mod:`domain.source_roots`'s rule, and asking it is the adapter's
(``adapters/retrodeck_folders.py``).

Pure compute — no I/O, no state mutation.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

_ESDE = "ES-DE"
_GAMELISTS = "gamelists"
_SCRIPTS = "scripts"
_DOWNLOADED_MEDIA = "downloaded_media"

# What a refusal of these folders is for, which names it in the sentence.
ESDE_LIBRARY = "Games in ES-DE"


@dataclass(frozen=True, slots=True)
class EsdeFolders:
    """Where ES-DE reads its gamelists, its event scripts and its game media from."""

    gamelists: str
    scripts: str
    downloaded_media: str


def esde_folders_under(home: str) -> EsdeFolders:
    """ES-DE's folders below RetroDECK's *home*."""
    root = os.path.join(home, _ESDE)
    return EsdeFolders(
        gamelists=os.path.join(root, _GAMELISTS),
        scripts=os.path.join(root, _SCRIPTS),
        downloaded_media=os.path.join(root, _DOWNLOADED_MEDIA),
    )
