"""``EsdeMediaIndexAdapter`` on ``tmp_path``: the record of the media Tender linked into ES-DE, under the data root."""

from __future__ import annotations

import logging
import os

import pytest

from adapters.esde_media_index import EsdeMediaIndexAdapter
from domain.esde_media_record import FolderMedia, GameMedia

_GAME = GameMedia(
    platform_slug="gba",
    system="gba",
    stem="Game",
    updated_at="2026-01-01T00:00:00+00:00",
    folders={
        "covers": FolderMedia(
            server_path="/assets/romm/resources/roms/1/42/cover/big.png",
            fingerprint='["/assets/romm/resources/roms/1/42/cover/big.png", null]',
            etag='"v1"',
            last_modified=None,
            extension=".png",
            link_path="/media/gba/covers/Game.png",
        )
    },
)


@pytest.fixture
def adapter(tmp_path, logger) -> EsdeMediaIndexAdapter:
    return EsdeMediaIndexAdapter(data_dir=str(tmp_path / "data"), logger=logger)


class TestTheRecord:
    def test_none_written_yet_reads_as_empty(self, adapter):
        assert adapter.read() == {}

    def test_it_reads_back_what_was_written_from_the_data_root(self, adapter, tmp_path):
        adapter.write({42: _GAME})

        assert adapter.read() == {42: _GAME}
        assert os.listdir(tmp_path / "data" / "esde") == ["media.json"]

    def test_a_write_replaces_the_record_whole(self, adapter):
        adapter.write({42: _GAME, 7: _GAME})

        adapter.write({7: _GAME})

        assert adapter.read() == {7: _GAME}

    def test_one_it_cannot_read_reads_as_empty_and_is_logged(self, adapter, tmp_path, caplog):
        path = tmp_path / "data" / "esde" / "media.json"
        path.parent.mkdir(parents=True)
        path.write_text("{ not json", encoding="utf-8")

        with caplog.at_level(logging.WARNING):
            assert adapter.read() == {}
        assert any(str(path) in record.getMessage() for record in caplog.records)

    def test_one_that_is_no_file_reads_as_empty_and_is_logged(self, adapter, tmp_path, caplog):
        (tmp_path / "data" / "esde" / "media.json").mkdir(parents=True)

        with caplog.at_level(logging.WARNING):
            assert adapter.read() == {}
        assert caplog.records

    def test_a_write_that_fails_leaves_no_temporary_file(self, adapter, tmp_path):
        (tmp_path / "data" / "esde" / "media.json").mkdir(parents=True)

        with pytest.raises(OSError):
            adapter.write({42: _GAME})

        assert os.listdir(tmp_path / "data" / "esde") == ["media.json"]
