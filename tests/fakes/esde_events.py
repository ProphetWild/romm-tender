"""ES-DE's side of an event script's call: how it escapes a game's path, and the command line it hands the shell.

Both are ported from ES-DE's source so a test holds Tender's half to what a
script really receives: ``Utils::FileSystem::getGenericPath`` and
``getEscapedPath`` (es-core/src/utils/FileSystemUtil.cpp), and the line
``Scripting::fireEvent`` (es-core/src/Scripting.cpp) passes to ``system()``,
which runs it with ``/bin/sh -c``.
"""

from __future__ import annotations

import subprocess
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping

# The characters getEscapedPath puts a backslash before, in its own order.
ESCAPED_BY_ESDE = "\\ '\"!$^&*(){}[]?;<>"


def generic_path(path: str) -> str:
    """``getGenericPath``: backslashes become slashes, and a ``//`` and a trailing ``/`` go."""
    if path.startswith("\\\\?\\"):
        path = path[4:]
    path = path.replace("\\", "/")
    while "//" in path:
        path = path.replace("//", "/")
    while len(path) > 1 and path.endswith("/"):
        path = path[:-1]
    return path


def escaped_path(path: str) -> str:
    """``getEscapedPath`` on Linux, ported from its C++ loop."""
    escaped = generic_path(path)
    for char in ESCAPED_BY_ESDE:
        start = 0
        while (offset := escaped.find(char, start)) != -1:
            start = offset + 1
            if offset == 0 or escaped[offset - 1] != "\\":
                escaped = escaped[:offset] + "\\" + escaped[offset:]
                start += 1
    return escaped


def fire_event(
    script: str, *arguments: str, env: Mapping[str, str], timeout: float = 30
) -> subprocess.CompletedProcess[str]:
    """Run *script* as ``Scripting::fireEvent`` does: each argument in double quotes unless empty, through ``sh -c``."""
    quoted = [f'"{argument}"' if argument and not argument.startswith('"') else argument for argument in arguments]
    command = " ".join([f'"{script}"', *quoted])
    return subprocess.run(
        ["/bin/sh", "-c", command], env=dict(env), capture_output=True, text=True, timeout=timeout, check=False
    )
