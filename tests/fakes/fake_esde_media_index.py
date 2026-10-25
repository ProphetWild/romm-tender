"""In-memory ``EsdeMediaIndexStore`` for service tests."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping

    from domain.esde_media_record import GameMedia


class FakeEsdeMediaIndex:
    """The record as a dict; ``reads`` and ``writes`` count each, and ``fail_write`` makes a write raise ``OSError``."""

    def __init__(self, index: Mapping[int, GameMedia] | None = None) -> None:
        self.index: dict[int, GameMedia] = dict(index or {})
        self.reads = 0
        self.writes = 0
        self.fail_write = False

    def read(self) -> dict[int, GameMedia]:
        self.reads += 1
        return dict(self.index)

    def write(self, index: Mapping[int, GameMedia]) -> None:
        if self.fail_write:
            raise OSError("cannot write the record")
        self.writes += 1
        self.index = dict(index)
