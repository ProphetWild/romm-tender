"""The event scripts Tender puts in ES-DE's scripts folder, which call this backend's hooks.

ES-DE runs every entry of ``<scripts>/<event>/`` on the event, in sorted order,
as ``"<script>" "<arg1>" … "<arg4>"`` through ``/bin/sh -c``, and ignores its
exit code (``Scripting::fireEvent``, es-core/src/Scripting.cpp); ``game-start``
holds the launch and ``startup`` holds ES-DE until their scripts finish. ES-DE
runs inside RetroDECK's flatpak, whose runtime has ``curl``, and a script finds
this backend by the hook files the host writes under the data root once its
port is bound and removes when it stops (``host/hooks.py`` says why there).

A script never fails ES-DE: it asks nothing and exits 0 where the files, the
port, ``curl`` or its path is missing, discards what ``curl`` prints, and exits
0 whatever ``curl`` answers. ``game-end`` and ``game-select`` call in the
background, so leaving a game never waits on the post-exit sync and a selection
never waits at all. A script is Tender's when its second line is
:data:`MARKER`; nothing else in the folder is ever replaced or removed.

Pure compute — no I/O.
"""

from __future__ import annotations

import os
import shlex
from dataclasses import dataclass

SCRIPT_NAME = "tender-romm.sh"

MARKER = "# tender-romm: written by Tender, which rewrites or removes this file; an edit here is lost."


@dataclass(frozen=True, slots=True)
class EsdeHookAddress:
    """Where a script finds this backend.

    The file holding its port, the file holding the one header line that admits
    a script, and the path each hook's name is appended to.
    """

    port_file: str
    header_file: str
    route_prefix: str


@dataclass(frozen=True, slots=True)
class HookScript:
    """One event's script: the ES-DE event it runs on, the hook it calls, and its text."""

    event: str
    hook: str
    text: str


@dataclass(frozen=True, slots=True)
class _Call:
    event: str
    hook: str
    what_for: str
    sends_path: bool
    escaped: bool
    max_time: int
    in_background: bool


# ES-DE passes ``game-start`` and ``game-end`` the escaped path and
# ``game-select`` the plain one (``domain/esde_hook_path.py``). ``prepare``
# downloads, so its bound is hours; ``startup`` outlasts the backend's own bound
# on it so the backend answers first.
_CALLS = (
    _Call(
        event="startup",
        hook="startup",
        what_for="as it starts, before it reads a gamelist, and waits. Tender writes the gamelists it held back.",
        sends_path=False,
        escaped=False,
        max_time=20,
        in_background=False,
    ),
    _Call(
        event="game-start",
        hook="prepare",
        what_for="when a game starts, and waits. Tender downloads the game if it has to, then pulls its saves.",
        sends_path=True,
        escaped=True,
        max_time=4 * 60 * 60,
        in_background=False,
    ),
    _Call(
        event="game-end",
        hook="finish",
        what_for="when a game ends, and does not wait. Tender pushes the game's saves.",
        sends_path=True,
        escaped=True,
        max_time=60 * 60,
        in_background=True,
    ),
    _Call(
        event="game-select",
        hook="select",
        what_for="when the cursor stops on a game, and does not wait. Tender fetches the game's media.",
        sends_path=True,
        escaped=False,
        max_time=10,
        in_background=True,
    ),
)


def hook_scripts(address: EsdeHookAddress) -> tuple[HookScript, ...]:
    """Every event's script, each calling its hook at *address*."""
    return tuple(HookScript(event=call.event, hook=call.hook, text=_script(call, address)) for call in _CALLS)


def script_path(scripts_folder: str, event: str) -> str:
    """Where *event*'s script of Tender's lies in ES-DE's *scripts_folder*."""
    return os.path.join(scripts_folder, event, SCRIPT_NAME)


def carries_marker(text: str) -> bool:
    """Whether *text*, a script's beginning, is a script Tender wrote: a shebang, then :data:`MARKER`."""
    lines = text.split("\n", 2)
    return len(lines) >= 2 and lines[0].startswith("#!") and lines[1] == MARKER


def _script(call: _Call, address: EsdeHookAddress) -> str:
    arguments = [
        "-sS",
        "--noproxy",
        "'*'",
        "--max-time",
        str(call.max_time),
        "-G",
        "-H",
        '"@$header_file"',
    ]
    if call.sends_path:
        arguments += ["--data-urlencode", '"path=$1"']
    if call.escaped:
        arguments += ["--data-urlencode", '"escaped=1"']
    arguments.append('"http://127.0.0.1:$port"' + shlex.quote(address.route_prefix + call.hook))
    request = "curl " + " ".join(arguments)
    lines = [
        "#!/bin/sh",
        MARKER,
        f"# ES-DE runs this {call.what_for}",
        "# It always exits 0, so ES-DE goes on whatever happens here.",
        f"port_file={shlex.quote(address.port_file)}",
        f"header_file={shlex.quote(address.header_file)}",
    ]
    if call.sends_path:
        lines.append('[ -n "$1" ] || exit 0')
    lines += [
        "command -v curl >/dev/null 2>&1 || exit 0",
        '[ -r "$header_file" ] || exit 0',
        'port=$(cat "$port_file" 2>/dev/null) || exit 0',
        "case $port in ''|*[!0-9]*) exit 0 ;; esac",
        f"({request}) >/dev/null 2>&1 &" if call.in_background else f"{request} >/dev/null 2>&1",
        "exit 0",
    ]
    return "\n".join(lines) + "\n"
