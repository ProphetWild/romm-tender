"""ES-DE's gamelist files: read as the text they hold, and written whole.

Owns the file operations on ``<gamelists>/<system>/gamelist.xml``. What goes
into a gamelist is ``domain/esde_gamelist.py``'s merge, over the text read
here, and when it is written is the gamelist service's.

ES-DE, its scraper and the user write the same files, so the text is read and
written as it is — no newline translation, the byte order mark kept — and a
write replaces the file whole through a temporary file in its own folder, so
ES-DE never reads half of one. The file keeps its mode, and a gamelist that is
a link is written where the link points, so the link stays.
"""

from __future__ import annotations

import contextlib
import os
import stat


class EsdeGamelistFilesAdapter:
    """Reads and writes ES-DE's gamelist files.

    Implements the ``EsdeGamelistStore`` Protocol structurally.
    """

    def read_gamelist(self, path: str) -> str | None:
        """The text of the gamelist at *path*, or ``None`` where no file is there.

        Raises ``OSError`` where it cannot be read and ``ValueError`` where it
        is no UTF-8 text, the encoding ES-DE writes it in.
        """
        try:
            with open(path, encoding="utf-8", newline="") as handle:
                return handle.read()
        except FileNotFoundError:
            return None

    def write_gamelist(self, path: str, text: str) -> None:
        """Replace the gamelist at *path* with *text*, creating its folder where it is missing.

        Raises ``OSError`` where it cannot be written and ``ValueError`` for
        text UTF-8 cannot carry; either way the file is left as it was.
        """
        target = os.path.realpath(path)
        folder = os.path.dirname(target)
        os.makedirs(folder, exist_ok=True)
        try:
            mode: int | None = stat.S_IMODE(os.stat(target).st_mode)
        except FileNotFoundError:
            mode = None
        temporary = os.path.join(folder, f".{os.path.basename(target)}.{os.getpid()}.tmp")
        try:
            with open(temporary, "w", encoding="utf-8", newline="") as handle:
                handle.write(text)
                handle.flush()
                os.fsync(handle.fileno())
            if mode is not None:
                os.chmod(temporary, mode)
            os.replace(temporary, target)
        except BaseException:
            with contextlib.suppress(OSError):
                os.remove(temporary)
            raise
