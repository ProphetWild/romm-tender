"""ES-DE process adapter — concrete ``EsdeRunningFn`` reading the process table directly.

Answers whether an ES-DE process runs, which decides whether Tender may write
ES-DE's gamelists now or must leave them to ES-DE's next start. Reads
``/proc/<pid>/comm`` and ``cmdline`` as ``renderer_rss`` reads them, with no
subprocess. RetroDECK's flatpak runs ES-DE in a sandbox of its own, but its
processes are in the host's table all the same.

It fails safe the other way from that sibling: a table that cannot be read at
all answers that ES-DE runs, so a gamelist ES-DE may be holding is never
written. One process that is gone between the listing and its read is the
ordinary race and is passed over.
"""

from __future__ import annotations

import os

_PROC = "/proc"
_ESDE = "es-de"


class EsdeProcessAdapter:
    """Real ``EsdeRunningFn`` over the process table under *proc_root*."""

    def __init__(self, *, proc_root: str = _PROC) -> None:
        self._root = proc_root

    def __call__(self) -> bool:
        """Whether a process named ``es-de``, in any case, runs; ``True`` where the table cannot be listed."""
        try:
            entries = os.listdir(self._root)
        except OSError:
            return True
        return any(self._is_esde(entry) for entry in entries if entry.isdigit())

    def _is_esde(self, pid: str) -> bool:
        """Whether *pid* runs ES-DE, by its name or, failing that, by the program its command line starts.

        ``comm`` is whatever the main thread last named itself
        (``prctl(PR_SET_NAME)``), so the program ``argv[0]`` names is asked too.
        """
        folder = os.path.join(self._root, pid)
        try:
            with open(os.path.join(folder, "comm"), encoding="utf-8", errors="replace") as handle:
                if handle.read().strip().lower() == _ESDE:
                    return True
        except OSError:
            pass
        try:
            with open(os.path.join(folder, "cmdline"), "rb") as handle:
                program = handle.read().split(b"\0", 1)[0]
        except OSError:
            return False
        return os.path.basename(os.fsdecode(program)).lower() == _ESDE
