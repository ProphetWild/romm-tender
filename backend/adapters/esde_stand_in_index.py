"""The record of the stand-ins Tender placed, ``<data>/esde/index.json``.

Owns reading and writing the file. What it holds and how it is spelled is
``domain/esde_stand_in.py``'s; what goes into it is the service's. It is kept
in the data root rather than the cache root because it is not re-derivable:
it is how a stand-in is put back after an uninstall, and which links are
Tender's to remove.
"""

from __future__ import annotations

import contextlib
import os
from typing import TYPE_CHECKING

from domain.esde_stand_in import decode_index, encode_index

if TYPE_CHECKING:
    import logging
    from collections.abc import Mapping

    from domain.esde_stand_in import StandInRecord

_INDEX_PATH = ("esde", "index.json")


class EsdeStandInIndexAdapter:
    """Reads and writes the record under *data_dir*.

    Implements the ``EsdeStandInIndexStore`` Protocol structurally.
    """

    def __init__(self, *, data_dir: str, logger: logging.Logger) -> None:
        self._path = os.path.join(data_dir, *_INDEX_PATH)
        self._logger = logger

    def read(self) -> dict[int, StandInRecord]:
        """The record, empty where there is none yet or it cannot be read — which is logged. Never raises."""
        try:
            with open(self._path, encoding="utf-8") as f:
                raw = f.read()
        except FileNotFoundError:
            return {}
        except (OSError, UnicodeDecodeError) as e:
            self._logger.warning(f"[esde] {self._path} could not be read, so no stand-in is recorded: {e!r}")
            return {}
        index = decode_index(raw)
        if index is None:
            self._logger.warning(f"[esde] {self._path} is no record of stand-ins, so none is recorded")
            return {}
        return index

    def write(self, index: Mapping[int, StandInRecord]) -> None:
        """Replace the record with *index*, through a temporary file so no reader sees half of one.

        Raises ``OSError`` where it could not be written.
        """
        os.makedirs(os.path.dirname(self._path), exist_ok=True)
        temporary = f"{self._path}.{os.getpid()}.tmp"
        try:
            with open(temporary, "w", encoding="utf-8") as f:
                f.write(encode_index(index))
            os.replace(temporary, self._path)
        except BaseException:
            with contextlib.suppress(OSError):
                os.remove(temporary)
            raise
