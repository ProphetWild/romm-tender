"""Tests for ``domain/esde_media_record.py`` — the record of the media Tender linked into ES-DE, and its rules."""

from __future__ import annotations

import dataclasses
import json
from typing import TYPE_CHECKING, Any, cast

import pytest

from domain.esde_media import media_fingerprint
from domain.esde_media_record import (
    FolderMedia,
    GameMedia,
    decode_media_index,
    encode_media_index,
    is_current,
    on_demand_evictions,
    revalidates,
    with_source,
)

if TYPE_CHECKING:
    from domain.esde_media import MediaFolder

_COVER = "/assets/romm/resources/roms/1/10/cover/big.png?ts=2026-01-01 00:00:00"


_BASE = FolderMedia(
    server_path=_COVER,
    fingerprint=media_fingerprint(_COVER, "2026-01-01T00:00:00+00:00"),
    etag='"v1"',
    last_modified=None,
    extension=".png",
    link_path="/media/gba/covers/Game.png",
)


def _media(**changes: Any) -> FolderMedia:
    return dataclasses.replace(_BASE, **changes)


def _game(**folders: FolderMedia) -> GameMedia:
    return GameMedia(
        platform_slug="gba",
        system="gba",
        stem="Game",
        updated_at="2026-01-01T00:00:00+00:00",
        folders=cast("dict[MediaFolder, FolderMedia]", folders),
    )


class TestWhetherACachedCopyIsCurrent:
    def test_a_copy_fetched_for_the_same_fingerprint_that_is_still_there_is(self):
        media = _media()

        assert is_current(media, media.fingerprint or "", cached=True)

    def test_a_copy_that_is_gone_is_not(self):
        media = _media()

        assert not is_current(media, media.fingerprint or "", cached=False)

    def test_another_fingerprint_is_not(self):
        assert not is_current(_media(), media_fingerprint(_COVER, "2026-02-01T00:00:00+00:00"), cached=True)

    def test_nothing_fetched_yet_is_not(self):
        assert not is_current(None, "x", cached=False)
        assert not is_current(_media(fingerprint=None, extension=None), "x", cached=False)

    def test_a_file_rom_m_did_not_serve_for_the_same_fingerprint_stays_settled_without_a_copy(self):
        # A 404, or bytes ES-DE cannot read: asked again only once the fingerprint moves.
        media = _media(extension=None, link_path=None)

        assert is_current(media, media.fingerprint or "", cached=False)


class TestWhenAFetchMayRevalidate:
    def test_the_same_file_with_a_validator_and_a_copy_does(self):
        assert revalidates(_media(), _COVER, cached=True)

    def test_a_path_moved_by_its_ts_alone_is_the_same_file(self):
        assert revalidates(_media(), _COVER.replace("2026-01-01", "2026-03-01"), cached=True)

    def test_another_file_does_not(self):
        assert not revalidates(_media(), "/assets/romm/resources/roms/1/10/cover/small.png", cached=True)

    def test_without_a_copy_or_a_validator_it_does_not(self):
        assert not revalidates(_media(), _COVER, cached=False)
        assert not revalidates(_media(etag=None, last_modified=None), _COVER, cached=True)
        assert not revalidates(_media(extension=None), _COVER, cached=True)
        assert not revalidates(None, _COVER, cached=True)

    def test_a_last_modified_alone_is_a_validator(self):
        assert revalidates(_media(etag=None, last_modified="Wed, 01 Jan 2025 00:00:00 GMT"), _COVER, cached=True)


class TestANewSourceForAFolderFetchedOnSelection:
    def test_the_same_file_keeps_what_is_cached_and_its_validators(self):
        media = _media(last_selected=5.0, size_bytes=10)

        moved = with_source(media, _COVER.replace("2026-01-01", "2026-03-01"))

        assert moved.server_path == _COVER.replace("2026-01-01", "2026-03-01")
        assert (moved.fingerprint, moved.etag, moved.extension, moved.size_bytes) == (
            media.fingerprint,
            '"v1"',
            ".png",
            10,
        )

    def test_another_file_keeps_what_is_cached_and_drops_the_validators(self):
        moved = with_source(_media(last_modified="x"), "/assets/romm/resources/roms/1/10/video/other.mp4")

        assert (moved.etag, moved.last_modified) == (None, None)
        assert moved.extension == ".png"

    def test_a_folder_never_recorded_starts_with_its_source_alone(self):
        assert with_source(None, _COVER) == FolderMedia(
            server_path=_COVER, fingerprint=None, etag=None, last_modified=None, extension=None, link_path=None
        )


class TestWhatTheCapEvicts:
    def test_the_least_recently_selected_go_until_the_rest_fits(self):
        index = {
            1: _game(videos=_media(last_selected=1.0, size_bytes=60), manuals=_media(last_selected=1.0, size_bytes=5)),
            2: _game(videos=_media(last_selected=2.0, size_bytes=50)),
            3: _game(covers=_media(size_bytes=500)),
        }

        assert on_demand_evictions(index, 100) == [(1, "manuals"), (1, "videos")]

    def test_nothing_goes_while_everything_fits(self):
        assert on_demand_evictions({1: _game(videos=_media(last_selected=1.0, size_bytes=60))}, 100) == []

    def test_a_folder_with_nothing_cached_counts_for_nothing(self):
        index = {1: _game(videos=_media(extension=None, last_selected=1.0, size_bytes=None))}

        assert on_demand_evictions(index, 0) == []

    def test_a_copy_never_selected_goes_first(self):
        index = {
            1: _game(videos=_media(last_selected=None, size_bytes=60)),
            2: _game(videos=_media(last_selected=1.0, size_bytes=60)),
        }

        assert on_demand_evictions(index, 100) == [(1, "videos")]


class TestTheRecordOnDisk:
    def test_what_is_written_reads_back_the_same(self):
        index = {
            7: _game(
                covers=_media(),
                videos=_media(
                    server_path="/assets/romm/resources/roms/1/7/video/v.mp4",
                    extension=".mp4",
                    link_path="/media/gba/videos/Game.mp4",
                    last_selected=1767225600.5,
                    size_bytes=1234,
                ),
                manuals=_media(fingerprint=None, etag=None, extension=None, link_path=None),
            )
        }

        raw = encode_media_index(index)

        assert json.loads(raw)["version"] == 1
        assert decode_media_index(raw) == index

    def test_an_empty_record_reads_back_empty(self):
        assert decode_media_index(encode_media_index({})) == {}

    @pytest.mark.parametrize(
        "raw",
        [
            "not json",
            "[]",
            json.dumps({"version": 2, "games": {}}),
            json.dumps({"version": 1, "games": []}),
            json.dumps({"version": 1, "games": {"x": {}}}),
            json.dumps({"version": 1, "games": {"1": {"platform_slug": "gba"}}}),
            json.dumps(
                {
                    "version": 1,
                    "games": {
                        "1": {"platform_slug": "gba", "system": "gba", "stem": "G", "updated_at": None, "folders": []}
                    },
                }
            ),
            json.dumps(
                {
                    "version": 1,
                    "games": {
                        "1": {
                            "platform_slug": "gba",
                            "system": "gba",
                            "stem": "G",
                            "updated_at": None,
                            "folders": {"custom": {"server_path": "/x"}},
                        }
                    },
                }
            ),
            json.dumps(
                {
                    "version": 1,
                    "games": {
                        "1": {
                            "platform_slug": "gba",
                            "system": "gba",
                            "stem": "G",
                            "updated_at": None,
                            "folders": {"covers": {"server_path": 3}},
                        }
                    },
                }
            ),
            json.dumps(
                {
                    "version": 1,
                    "games": {
                        "1": {
                            "platform_slug": "gba",
                            "system": "gba",
                            "stem": "G",
                            "updated_at": None,
                            "folders": {"videos": {"server_path": "/v", "size_bytes": "big"}},
                        }
                    },
                }
            ),
        ],
    )
    def test_anything_else_is_no_record(self, raw):
        assert decode_media_index(raw) is None
