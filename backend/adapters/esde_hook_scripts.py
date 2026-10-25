"""Tender's event scripts in ES-DE's scripts folder, written and removed.

Owns every file operation on a script of Tender's. What a script says, where it
goes and how Tender's are told from anybody else's is
``domain/esde_hook_scripts.py``'s; when they are written is the service's.

**Nothing but a script of Tender's is ever replaced or removed.** The file at the
name is read again first without following a link, and is replaced or removed
only while it is a regular file whose beginning carries Tender's marker. A
user's script, a link and a folder at the name are left exactly as they are.
"""

from __future__ import annotations

import contextlib
import os
import stat

from domain.esde_hook_scripts import carries_marker

# Enough of a file to read its shebang and marker lines.
_HEAD_BYTES = 4096


class EsdeHookScriptsAdapter:
    """Writes and removes the scripts of Tender's; implements the ``EsdeHookScriptStore`` Protocol structurally."""

    def install(self, path: str, text: str) -> bool:
        """Write *text* at *path* as an executable script, where nothing is there or a script of Tender's is.

        Answers whether it was written. Written through a temporary beside it and
        renamed over the old one, so ES-DE never runs half a script. Raises
        ``OSError`` where it cannot be written.
        """
        if os.path.lexists(path) and not self.holds(path):
            return False
        folder = os.path.dirname(path)
        os.makedirs(folder, exist_ok=True)
        temporary = os.path.join(folder, f".{os.path.basename(path)}.{os.getpid()}.tmp")
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW | os.O_CLOEXEC, 0o755)
        try:
            os.fchmod(fd, 0o755)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(text)
            os.replace(temporary, path)
        except BaseException:
            with contextlib.suppress(OSError):
                os.unlink(temporary)
            raise
        return True

    def remove(self, path: str) -> bool:
        """Remove *path* if it is a script of Tender's; whether it was removed.

        Raises ``OSError`` where it cannot be removed.
        """
        if not self.holds(path):
            return False
        os.unlink(path)
        return True

    def holds(self, path: str) -> bool:
        """Whether *path* is a regular file, not followed if it is a link, that begins as a script of Tender's."""
        try:
            if not stat.S_ISREG(os.lstat(path).st_mode):
                return False
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
            with os.fdopen(fd, "rb") as handle:
                head = handle.read(_HEAD_BYTES)
        except OSError:
            return False
        return carries_marker(head.decode("utf-8", errors="replace"))
