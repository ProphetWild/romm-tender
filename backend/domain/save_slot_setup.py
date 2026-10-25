"""The save slot a start with nobody to ask confirms by itself, read off ``get_save_setup_info``'s answer.

The panel's Steam launch path makes the same decision (``resolveSaveSetupOutcome``
in frontend/src/utils/saveSetup.ts) and confirms the slot without asking; a start
from ES-DE has nobody to ask either. Where the server's slots could not be read,
an empty list of them says nothing about the server, and confirming the default
then could clobber real server saves on the first sync, so nothing is confirmed.

Pure compute — no I/O.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Mapping

_SERVER_NOT_READ = frozenset({"server_unreachable", "not_found", "romm_refused", "device_not_registered"})


def slot_to_confirm(setup_info: Mapping[str, Any]) -> str | None:
    """The slot to confirm for *setup_info*'s game, or ``None`` where that is the user's choice or unknowable.

    The default slot where the answer recommends it, or where the server
    answered and holds no slot; ``None`` where the server holds slots to choose
    between or its slots could not be read.
    """
    action = setup_info.get("recommended_action")
    if setup_info.get("server_query_failed") or action in _SERVER_NOT_READ:
        return None
    slot = setup_info.get("default_slot")
    if not isinstance(slot, str) or not slot:
        return None
    if action == "auto_confirm_default" or not setup_info.get("server_slots"):
        return slot
    return None
