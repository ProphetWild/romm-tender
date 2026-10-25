"""ES-DE's event scripts as Tender writes them, run the way ES-DE runs them.

Every script runs under ``/bin/sh`` with ``HOME`` a ``tmp_path`` and a ``PATH``
of a stub directory alone: ``cat`` is linked in from the system, and ``curl`` is
a stand-in that records its arguments, or the real one where a case says so.
Nothing reaches a network but the loopback server a case starts itself.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import TYPE_CHECKING, Any, ClassVar

import pytest
from fakes.esde_events import escaped_path, fire_event

from domain.esde_hook_path import unescape_esde_path
from domain.esde_hook_scripts import (
    MARKER,
    SCRIPT_NAME,
    EsdeHookAddress,
    carries_marker,
    hook_scripts,
    script_path,
)
from host.hooks import ESDE_HOOKS_PREFIX, hook_arguments

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

_TOKEN = "the-hook-token"
_PORT = 27737

_STUB_CURL = """\
#!{python}
import json, os, sys, time
with open(os.environ["STUB_CURL_LOG"], "a", encoding="utf-8") as log:
    log.write(json.dumps(sys.argv[1:]) + "\\n")
time.sleep(float(os.environ.get("STUB_CURL_SLEEP", "0")))
with open(os.environ["STUB_CURL_LOG"] + ".ended", "a", encoding="utf-8") as ended:
    ended.write("ended\\n")
sys.exit(int(os.environ.get("STUB_CURL_EXIT", "0")))
"""

_EVENTS = {"startup": "startup", "game-start": "prepare", "game-end": "finish", "game-select": "select"}


@dataclass
class Machine:
    """A home with the hook files Tender writes, ES-DE's scripts folder, and the stubs a script finds on ``PATH``."""

    home: Path
    hook_dir: Path
    scripts: Path
    stubs: Path
    curl_log: Path

    @property
    def address(self) -> EsdeHookAddress:
        return EsdeHookAddress(
            port_file=str(self.hook_dir / "port"),
            header_file=str(self.hook_dir / "header"),
            route_prefix=ESDE_HOOKS_PREFIX,
        )

    def write_hook_files(self, port: int = _PORT) -> None:
        self.hook_dir.mkdir(parents=True, exist_ok=True)
        (self.hook_dir / "header").write_text(f"X-Tender-Hook-Token: {_TOKEN}\n", encoding="utf-8")
        (self.hook_dir / "port").write_text(str(port), encoding="utf-8")

    def install_scripts(self) -> dict[str, str]:
        """Write every script where ES-DE looks for it, executable; answer each event's script path."""
        written: dict[str, str] = {}
        for script in hook_scripts(self.address):
            path = script_path(str(self.scripts), script.event)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(script.text)
            os.chmod(path, 0o755)
            written[script.event] = path
        return written

    def env(self, **extra: str) -> dict[str, str]:
        return {"HOME": str(self.home), "PATH": str(self.stubs), "STUB_CURL_LOG": str(self.curl_log), **extra}

    def calls(self) -> list[list[str]]:
        if not self.curl_log.exists():
            return []
        return [json.loads(line) for line in self.curl_log.read_text(encoding="utf-8").splitlines()]


@pytest.fixture
def machine(tmp_path: Path) -> Machine:
    # Inside the home on purpose: a data root with a space in its path.
    home = tmp_path / "home dir"
    stubs = tmp_path / "stubs"
    stubs.mkdir()
    os.symlink(shutil.which("cat") or "/bin/cat", stubs / "cat")
    curl = stubs / "curl"
    curl.write_text(_STUB_CURL.format(python=sys.executable), encoding="utf-8")
    curl.chmod(0o755)
    return Machine(
        home=home,
        hook_dir=home / ".local" / "share" / "romm-tender" / "esde-hooks",
        scripts=home / "retrodeck" / "ES-DE" / "scripts",
        stubs=stubs,
        curl_log=tmp_path / "curl.log",
    )


@pytest.fixture
def scripts(machine: Machine) -> dict[str, str]:
    machine.write_hook_files()
    return machine.install_scripts()


def _route(hook: str, port: int = _PORT) -> str:
    return f"http://127.0.0.1:{port}{ESDE_HOOKS_PREFIX}{hook}"


class TestTheScripts:
    def test_one_script_per_event_each_calling_its_hook(self, machine):
        assert {script.event: script.hook for script in hook_scripts(machine.address)} == _EVENTS

    def test_each_lies_in_its_events_folder_under_tenders_name(self):
        assert script_path("/rd/ES-DE/scripts", "game-start") == f"/rd/ES-DE/scripts/game-start/{SCRIPT_NAME}"

    def test_each_is_a_posix_shell_script_that_says_tender_wrote_it(self, machine):
        for script in hook_scripts(machine.address):
            first, second = script.text.splitlines()[:2]
            assert first == "#!/bin/sh"
            assert second == MARKER
            assert carries_marker(script.text)

    @pytest.mark.parametrize(
        "text",
        [
            "#!/bin/sh\necho mine\n",
            f"#!/bin/sh\n# mine\n{MARKER}\n",
            f"{MARKER}\n#!/bin/sh\n",
            "",
            f"#!/bin/bash\r\n{MARKER}\r\n",
        ],
    )
    def test_a_script_of_anybody_elses_carries_no_marker(self, text):
        assert not carries_marker(text)


class TestWhatEachScriptAsks:
    def test_game_start_asks_prepare_with_the_escaped_path(self, machine, scripts):
        ran = fire_event(scripts["game-start"], "/roms/snes/a\\ b.sfc", "A B", "snes", "SNES", env=machine.env())

        assert ran.returncode == 0
        assert machine.calls() == [
            [
                "-sS",
                "--noproxy",
                "*",
                "--max-time",
                "14400",
                "-G",
                "-H",
                f"@{machine.hook_dir / 'header'}",
                "--data-urlencode",
                "path=/roms/snes/a\\ b.sfc",
                "--data-urlencode",
                "escaped=1",
                _route("prepare"),
            ]
        ]

    def test_game_select_asks_select_with_the_plain_path_and_no_escaped_flag(self, machine, scripts):
        ran = fire_event(scripts["game-select"], "/roms/snes/a b.sfc", "A B", "snes", "SNES", env=machine.env())

        assert ran.returncode == 0
        [call] = _eventually(machine.calls)
        assert call[-3:] == ["--data-urlencode", "path=/roms/snes/a b.sfc", _route("select")]
        assert "escaped=1" not in call

    def test_game_end_asks_finish_with_the_escaped_path(self, machine, scripts):
        fire_event(scripts["game-end"], "/roms/snes/a\\ b.sfc", "A B", "snes", "SNES", env=machine.env())

        [call] = _eventually(machine.calls)
        assert call[-5:] == [
            "--data-urlencode",
            "path=/roms/snes/a\\ b.sfc",
            "--data-urlencode",
            "escaped=1",
            _route("finish"),
        ]

    def test_startup_asks_startup_with_nothing_in_its_query(self, machine, scripts):
        ran = fire_event(scripts["startup"], env=machine.env())

        assert ran.returncode == 0
        [call] = machine.calls()
        assert "--data-urlencode" not in call
        assert call[-1] == _route("startup")

    @pytest.mark.parametrize(
        "path",
        [
            '/roms/snes/Don\'t "Stop" $HOME `id`.sfc',
            "/roms/snes/back\\slash\\\\twice.sfc",
            "/roms/gba/Pokémon ★ (Rev 1).gba",
            "/roms/gba/name=with=equals&an@at.gba",
        ],
    )
    def test_the_path_reaches_curl_as_one_argument_untouched(self, machine, scripts, path):
        """Run straight, not through ES-DE's command line, so nothing but the script stands between."""
        ran = _run_script(scripts["game-select"], path, env=machine.env())

        assert ran.returncode == 0
        [call] = _eventually(machine.calls)
        assert f"path={path}" in call

    def test_the_port_file_names_the_port(self, machine, scripts):
        machine.write_hook_files(port=40001)

        fire_event(scripts["startup"], env=machine.env())

        assert machine.calls()[0][-1] == _route("startup", port=40001)

    @pytest.mark.parametrize("event", ["startup", "game-start"])
    def test_every_bound_is_generous_enough_for_its_hook(self, machine, scripts, event):
        fire_event(scripts[event], "/roms/a.sfc", env=machine.env())

        [call] = machine.calls()
        max_time = int(call[call.index("--max-time") + 1])
        assert max_time == (14400 if event == "game-start" else 20)


class TestEveryScriptAlwaysExitsZero:
    @pytest.mark.parametrize("event", list(_EVENTS))
    def test_with_no_hook_files_it_asks_nothing(self, machine, event):
        written = machine.install_scripts()

        ran = fire_event(written[event], "/roms/a.sfc", env=machine.env())

        assert ran.returncode == 0
        assert _settled(machine.calls) == []

    @pytest.mark.parametrize("event", list(_EVENTS))
    def test_with_only_the_port_file_it_asks_nothing(self, machine, scripts, event):
        (machine.hook_dir / "header").unlink()

        ran = fire_event(scripts[event], "/roms/a.sfc", env=machine.env())

        assert ran.returncode == 0
        assert _settled(machine.calls) == []

    @pytest.mark.parametrize("port", ["", "abc", "27737\n27738", "-1"])
    def test_with_a_port_file_that_names_no_port_it_asks_nothing(self, machine, scripts, port):
        (machine.hook_dir / "port").write_text(port, encoding="utf-8")

        ran = fire_event(scripts["game-start"], "/roms/a.sfc", env=machine.env())

        assert ran.returncode == 0
        assert machine.calls() == []

    @pytest.mark.parametrize("event", list(_EVENTS))
    def test_without_curl_it_asks_nothing(self, machine, scripts, event):
        (machine.stubs / "curl").unlink()

        ran = fire_event(scripts[event], "/roms/a.sfc", env=machine.env())

        assert ran.returncode == 0
        assert ran.stderr == ""

    @pytest.mark.parametrize("event", list(_EVENTS))
    def test_with_curl_failing(self, machine, scripts, event):
        ran = fire_event(scripts[event], "/roms/a.sfc", env=machine.env(STUB_CURL_EXIT="7"))

        assert ran.returncode == 0
        assert len(_eventually(machine.calls)) == 1

    @pytest.mark.parametrize("event", ["game-start", "game-end", "game-select"])
    def test_with_no_arguments_it_asks_nothing(self, machine, scripts, event):
        """ES-DE fires ``game-select`` with no arguments at all when a fast scroll starts."""
        ran = fire_event(scripts[event], env=machine.env())

        assert ran.returncode == 0
        assert _settled(machine.calls) == []

    def test_with_its_files_unreadable_as_a_directory_it_asks_nothing(self, machine, scripts):
        (machine.hook_dir / "port").unlink()
        (machine.hook_dir / "port").mkdir()

        ran = fire_event(scripts["game-start"], "/roms/a.sfc", env=machine.env())

        assert ran.returncode == 0
        assert machine.calls() == []


class TestWhatWaits:
    def test_leaving_a_game_never_waits_on_the_call(self, machine, scripts):
        started = time.monotonic()

        ran = fire_event(scripts["game-end"], "/roms/a.sfc", env=machine.env(STUB_CURL_SLEEP="3"), timeout=10)

        assert ran.returncode == 0
        assert time.monotonic() - started < 2
        assert len(_eventually(machine.calls)) == 1
        assert not (machine.curl_log.parent / "curl.log.ended").exists()

    def test_selecting_a_game_never_waits_on_the_call_either(self, machine, scripts):
        started = time.monotonic()

        fire_event(scripts["game-select"], "/roms/a.sfc", env=machine.env(STUB_CURL_SLEEP="3"), timeout=10)

        assert time.monotonic() - started < 2

    def test_starting_a_game_waits_for_the_call(self, machine, scripts):
        fire_event(scripts["game-start"], "/roms/a.sfc", env=machine.env(STUB_CURL_SLEEP="0.5"), timeout=10)

        assert (machine.curl_log.parent / "curl.log.ended").exists()


class _Recorder(BaseHTTPRequestHandler):
    """Answers every GET with an empty JSON object, recording its target and its hook token."""

    seen: ClassVar[list[tuple[str, str | None]]] = []

    def do_GET(self) -> None:
        type(self).seen.append((self.path, self.headers.get("X-Tender-Hook-Token")))
        body = b"{}"
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:
        return


@pytest.fixture
def server() -> Iterator[ThreadingHTTPServer]:
    _Recorder.seen = []
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Recorder)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield httpd
    finally:
        httpd.shutdown()
        httpd.server_close()


@pytest.mark.skipif(shutil.which("curl") is None, reason="needs a real curl")
class TestThroughARealCurl:
    """The real ``curl`` against a loopback server: what the host reads back is what ES-DE meant."""

    @pytest.fixture(autouse=True)
    def real_curl(self, machine: Machine, server: ThreadingHTTPServer) -> None:
        (machine.stubs / "curl").unlink()
        os.symlink(shutil.which("curl") or "", machine.stubs / "curl")
        machine.write_hook_files(port=server.server_address[1])

    @pytest.mark.parametrize(
        "path",
        [
            "/home/deck/retrodeck/roms/psx/Final Fantasy VII (USA).m3u/Final Fantasy VII (USA).m3u",
            '/roms/snes/Don\'t "Stop" $HOME & {Beta} <proto>; ok? [!]^*.sfc',
            "/roms/gba/Pokémon \u2013 Édition Rubis ★ 100%.gba",
            "/roms/gba/caf\udce9 latin-1.gba",
        ],
    )
    def test_a_game_start_reads_back_as_the_path_es_de_started(self, machine, path):
        written = machine.install_scripts()

        ran = fire_event(written["game-start"], escaped_path(path), "Game", "snes", "SNES", env=machine.env())

        assert ran.returncode == 0
        [(target, token)] = _Recorder.seen
        arguments = hook_arguments(target)
        assert target.startswith(f"{ESDE_HOOKS_PREFIX}prepare?")
        assert token == _TOKEN
        assert arguments["escaped"] == "1"
        assert unescape_esde_path(arguments["path"]) == path

    def test_a_game_select_reads_back_as_the_path_itself(self, machine):
        written = machine.install_scripts()
        path = "/roms/gba/Pokémon (Rev 1) & more.gba"

        fire_event(written["game-select"], path, "Game", "gba", "GBA", env=machine.env())

        [(target, token)] = _eventually(lambda: _Recorder.seen)
        assert token == _TOKEN
        assert hook_arguments(target) == {"path": path}


def _run_script(script: str, *arguments: str, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    """Run *script* straight, with nothing between it and its arguments."""
    return subprocess.run([script, *arguments], env=env, capture_output=True, text=True, timeout=30, check=False)


def _eventually[T](read: Any, *, within: float = 5.0) -> list[T]:
    """What *read* answers once it answers anything, for a call a script left running in the background."""
    deadline = time.monotonic() + within
    while time.monotonic() < deadline:
        answer = read()
        if answer:
            return list(answer)
        time.sleep(0.02)
    return list(read())


def _settled[T](read: Any, *, wait: float = 0.3) -> list[T]:
    """What *read* answers once a call a script may have left in the background had time to land."""
    time.sleep(wait)
    return list(read())
