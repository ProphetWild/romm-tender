"""In-memory ``EsdeGamelistStore`` and ``EsdeGamelistIndexStore`` for service tests."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping

    from domain.esde_gamelist_record import SystemGamelist


class FakeEsdeGamelistFiles:
    """Gamelist files as a dict of path to text; ``writes`` lists each path written, in order.

    A path in ``unreadable`` raises ``OSError`` when read, and one in
    ``unwritable`` when written.
    """

    def __init__(self, files: Mapping[str, str] | None = None) -> None:
        self.files: dict[str, str] = dict(files or {})
        self.writes: list[str] = []
        self.unreadable: set[str] = set()
        self.unwritable: set[str] = set()

    def read_gamelist(self, path: str) -> str | None:
        if path in self.unreadable:
            raise OSError(f"cannot read {path}")
        return self.files.get(path)

    def write_gamelist(self, path: str, text: str) -> None:
        if path in self.unwritable:
            raise OSError(f"cannot write {path}")
        self.writes.append(path)
        self.files[path] = text


class FakeEsdeGamelistIndex:
    """The record as a dict; ``reads`` and ``writes`` count each, and ``fail_write`` makes a write raise ``OSError``."""

    def __init__(self, index: Mapping[str, SystemGamelist] | None = None) -> None:
        self.index: dict[str, SystemGamelist] = dict(index or {})
        self.reads = 0
        self.writes = 0
        self.fail_write = False

    def read(self) -> dict[str, SystemGamelist]:
        self.reads += 1
        return dict(self.index)

    def write(self, index: Mapping[str, SystemGamelist]) -> None:
        if self.fail_write:
            raise OSError("cannot write the record")
        self.writes += 1
        self.index = dict(index)
