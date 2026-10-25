"""``EsdeGamelistIndexAdapter`` on ``tmp_path``: the record of ES-DE's gamelists, under the data root."""

from __future__ import annotations

import logging
import os

import pytest

from adapters.esde_gamelist_index import EsdeGamelistIndexAdapter
from domain.esde_gamelist_record import SystemGamelist

_SNES = SystemGamelist(pending={"./A.sfc": {"name": "A"}}, written={"./B.sfc": {"name": "B", "players": "2"}})


@pytest.fixture
def adapter(tmp_path, logger) -> EsdeGamelistIndexAdapter:
    return EsdeGamelistIndexAdapter(data_dir=str(tmp_path / "data"), logger=logger)


class TestTheRecord:
    def test_none_written_yet_reads_as_empty(self, adapter):
        assert adapter.read() == {}

    def test_it_reads_back_what_was_written_from_the_data_root(self, adapter, tmp_path):
        adapter.write({"snes": _SNES})

        assert adapter.read() == {"snes": _SNES}
        assert os.listdir(tmp_path / "data" / "esde") == ["gamelists.json"]

    def test_a_write_replaces_the_record_whole(self, adapter):
        adapter.write({"snes": _SNES, "gba": _SNES})

        adapter.write({"gba": _SNES})

        assert adapter.read() == {"gba": _SNES}

    def test_one_it_cannot_read_reads_as_empty_and_is_logged(self, adapter, tmp_path, caplog):
        path = tmp_path / "data" / "esde" / "gamelists.json"
        path.parent.mkdir(parents=True)
        path.write_text("{ not json", encoding="utf-8")

        with caplog.at_level(logging.WARNING):
            assert adapter.read() == {}
        assert any(str(path) in record.getMessage() for record in caplog.records)

    def test_one_that_is_no_file_reads_as_empty_and_is_logged(self, adapter, tmp_path, caplog):
        path = tmp_path / "data" / "esde" / "gamelists.json"
        path.mkdir(parents=True)

        with caplog.at_level(logging.WARNING):
            assert adapter.read() == {}
        assert any(str(path) in record.getMessage() for record in caplog.records)

    def test_a_write_that_fails_leaves_no_temporary_file(self, adapter, tmp_path):
        (tmp_path / "data" / "esde" / "gamelists.json").mkdir(parents=True)

        with pytest.raises(OSError):
            adapter.write({"snes": _SNES})

        assert os.listdir(tmp_path / "data" / "esde") == ["gamelists.json"]
