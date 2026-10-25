"""Contract tests for the switch to a surface that writes ES-DE: the next sync fetches every platform in full.

A platform the sync skips as unchanged hands the ES-DE pass none of RomM's
fields, so a library first synced under ``steam`` would get no media after the
switch. Driven over the real ``bootstrap()`` + SQLite, the real ES-DE pass and
a RetroDECK seeded under ``tmp_path``; only the RomM transport is the fake, and
the panel's acknowledgement of a Steam run's chunk is stood in for.
"""

from __future__ import annotations

import asyncio
import os
from typing import Any

import pytest

from domain.collection_sync_state import CollectionSyncState
from domain.sync_run_kind import SyncRunKind

pytestmark = pytest.mark.usefixtures("seeded_retrodeck")

PNG = b"\x89PNG\r\n\x1a\n" + bytes(56)
_RES = "/assets/romm/resources/"
_COVER = f"{_RES}roms/1/10/cover/big.png"
_LOGO = f"{_RES}roms/1/10/logo/logo.png"


def _serve_game(harness) -> None:
    harness.romm.platforms.append({"id": 1, "name": "GBA", "slug": "gba", "rom_count": 1})
    harness.settings["enabled_platforms"] = {"1": True}
    harness.romm.roms[10] = {
        "id": 10,
        "name": "Game",
        "fs_name": "Game (USA).gba",
        "fs_size_bytes": 8,
        "platform_id": 1,
        "platform_name": "GBA",
        "platform_slug": "gba",
        "platform_fs_slug": "gba",
        "has_simple_single_file": True,
        "updated_at": "2025-12-01T00:00:00+00:00",
        "path_cover_large": _COVER,
        "ss_metadata": {"logo_path": "roms/1/10/logo/logo.png"},
    }
    harness.romm.media_files[_COVER] = PNG
    harness.romm.media_files[_LOGO] = PNG


def _ack_with(bindings: dict[str, int]):
    async def _wait(_unit, event):
        event.set()
        return dict(bindings)

    return _wait


async def _drain() -> None:
    for _ in range(20):
        pending = [t for t in asyncio.all_tasks() if t is not asyncio.current_task() and not t.done()]
        if not pending:
            return
        await asyncio.gather(*pending, return_exceptions=True)
        await asyncio.sleep(0)


async def _sync(harness, run_id: str) -> None:
    sync = harness.app.services.sync_service
    assert sync._box.try_begin_run(run_id, kind=SyncRunKind.APPLY) is True
    await sync._orchestrator._do_sync_per_unit()
    await _drain()


def _link(harness, folder: str) -> str:
    return os.path.join(harness.retrodeck_home, "ES-DE", "downloaded_media", "gba", folder, "Game (USA).png")


def _platform_listings(harness) -> int:
    return sum(1 for name, _args, _kwargs in harness.romm.call_log if name == "list_roms")


async def _steam_library(harness) -> None:
    """A library synced under ``steam``, its platform stamped and its game bound to a shortcut."""
    harness.settings["library_surface"] = "steam"
    _serve_game(harness)
    harness.app.services.sync_service._chunk_dispatcher._wait_for_unit_complete = _ack_with({"10": 7777})
    await _sync(harness, "run-steam")
    with harness.uow_factory() as uow:
        assert uow.platform_sync_state.get("gba") is not None


async def test_a_platform_left_unchanged_since_a_steam_sync_is_skipped_under_steam(harness):
    await _steam_library(harness)
    listed = _platform_listings(harness)

    await _sync(harness, "run-steam-2")

    assert _platform_listings(harness) == listed


async def test_the_sync_after_a_switch_to_esde_fetches_the_platform_in_full_and_links_its_media(harness):
    await _steam_library(harness)
    listed = _platform_listings(harness)

    result = await harness.endpoints.set_library_surface("esde")
    await _drain()
    await _sync(harness, "run-esde")

    assert result == {"success": True}
    assert _platform_listings(harness) > listed
    assert os.path.islink(_link(harness, "covers"))
    assert os.path.islink(_link(harness, "marquees"))


async def test_the_sync_after_that_skips_the_platform_again(harness):
    await _steam_library(harness)
    await harness.endpoints.set_library_surface("esde")
    await _drain()
    await _sync(harness, "run-esde")
    listed = _platform_listings(harness)

    await _sync(harness, "run-esde-2")

    assert _platform_listings(harness) == listed


@pytest.mark.parametrize(("before", "after"), [("esde", "both"), ("both", "esde"), ("esde", "esde"), ("both", "steam")])
async def test_a_switch_between_surfaces_that_both_write_es_de_or_away_from_it_revokes_nothing(harness, before, after):
    harness.settings["library_surface"] = before
    _serve_game(harness)
    harness.app.services.sync_service._chunk_dispatcher._wait_for_unit_complete = _ack_with({"10": 7777})
    await _sync(harness, "run-1")

    await harness.endpoints.set_library_surface(after)
    await _drain()

    with harness.uow_factory() as uow:
        stamp: Any = uow.platform_sync_state.get("gba")
    assert stamp is not None
    assert stamp.skip_revoked is False


async def test_the_switch_from_steam_takes_every_collection_s_skip_away_too(harness):
    await _steam_library(harness)
    with harness.uow_factory() as uow:
        uow.collection_sync_state.save(
            CollectionSyncState.stamp(
                collection_id="5",
                collection_kind="standard",
                updated_at="2025-12-01T00:00:00+00:00",
                completed_at="2026-01-01T00:00:00+00:00",
                rom_count=1,
                member_rom_ids=(10,),
            )
        )

    await harness.endpoints.set_library_surface("esde")

    with harness.uow_factory() as uow:
        assert uow.collection_sync_state.has_any() is False
        stamp = uow.platform_sync_state.get("gba")
    assert stamp is not None
    assert stamp.skip_revoked is True
