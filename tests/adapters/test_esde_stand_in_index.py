"""``EsdeStandInIndexAdapter`` on ``tmp_path``: the record of what Tender placed, under the data root."""

from __future__ import annotations

import logging
import os

import pytest

from adapters.esde_stand_in_index import EsdeStandInIndexAdapter
from domain.esde_stand_in import FILE_SHAPE, StandInRecord, StandInShape

_RECORD = StandInRecord(
    platform_slug="gba",
    system="gba",
    updated_at="2026-01-01T00:00:00+00:00",
    path="/roms/gba/Game.gba",
    shape=StandInShape(name="Game.gba", kind=FILE_SHAPE),
)


@pytest.fixture
def adapter(tmp_path, logger) -> EsdeStandInIndexAdapter:
    return EsdeStandInIndexAdapter(data_dir=str(tmp_path / "data"), logger=logger)


class TestTheRecord:
    def test_none_written_yet_reads_as_empty(self, adapter):
        assert adapter.read() == {}

    def test_it_reads_back_what_was_written_from_the_data_root(self, adapter, tmp_path):
        adapter.write({42: _RECORD})

        assert adapter.read() == {42: _RECORD}
        assert (tmp_path / "data" / "esde" / "index.json").is_file()
        assert os.listdir(tmp_path / "data" / "esde") == ["index.json"]

    def test_a_write_replaces_the_record_whole(self, adapter):
        adapter.write({42: _RECORD, 7: _RECORD})

        adapter.write({7: _RECORD})

        assert adapter.read() == {7: _RECORD}

    def test_one_it_cannot_read_reads_as_empty_and_is_logged(self, adapter, tmp_path, caplog):
        path = tmp_path / "data" / "esde" / "index.json"
        path.parent.mkdir(parents=True)
        path.write_text("{ not json", encoding="utf-8")

        with caplog.at_level(logging.WARNING):
            assert adapter.read() == {}
        assert any(str(path) in record.getMessage() for record in caplog.records)

    def test_one_that_is_no_file_reads_as_empty_and_is_logged(self, adapter, tmp_path, caplog):
        (tmp_path / "data" / "esde" / "index.json").mkdir(parents=True)

        with caplog.at_level(logging.WARNING):
            assert adapter.read() == {}
        assert caplog.records
