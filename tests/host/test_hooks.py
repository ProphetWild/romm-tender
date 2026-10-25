"""ES-DE's hook route: which call a path names, what reaches the handler, what comes back, and the files a script reads.

The route cases go through a real socket with a target written by hand, as the
file route's do: what a script's ``curl`` puts on the wire is exactly the input
a helper would have tidied away.
"""

from __future__ import annotations

import json
import logging
import os
import stat

import pytest

from host.access import HOOK_TOKEN_HEADER
from host.dispatch import DEFAULT_PAYLOAD_LIMIT, CallDispatcher
from host.events import EventSink
from host.hooks import HOOK_HEADER_FILENAME, HOOK_PORT_FILENAME, HookFiles, answer_hook
from host.protocol import REASON_BACKEND_EXCEPTION, REASON_PAYLOAD_TOO_LARGE, TYPE_ERROR
from host.server import HostServer
from lib.http_messages import parse_request_head
from tests.host.conftest import HOOK_NAMES, SERVER_IDENTITY, FakeEndpoints, FakeHooks, free_port
from tests.host.ws_client import http_get

LOGGER = logging.getLogger("test_hooks")


async def call(port: int, hook_token: str, target: str, *, origin: str | None = None, **kwargs):
    """Make one hook request the way a script's ``curl -H @header`` does: no origin, the header from the file."""
    return await http_get(port, target, origin=origin, headers=[(HOOK_TOKEN_HEADER, hook_token)], **kwargs)


async def call_running(running_host, target: str, **kwargs):
    return await call(running_host.port, running_host.hook_token, target, **kwargs)


class TestAKnownHook:
    async def test_it_calls_its_handler_with_the_decoded_query(self, running_host):
        target = "/hooks/esde/prepare?path=%2Fhome%2Fdeck%2FROMs%2Fsnes%2FSuper%5C%20Mario%5C%20World.sfc&escaped=1"

        status, _, _ = await call_running(running_host, target)

        assert status == 200
        assert running_host.hooks.calls == [
            ("prepare", {"path": "/home/deck/ROMs/snes/Super\\ Mario\\ World.sfc", "escaped": "1"})
        ]

    @pytest.mark.parametrize(
        ("encoded", "decoded"),
        [
            ("Super%20Mario", "Super Mario"),
            ("Super+Mario", "Super Mario"),
            ("C%2B%2B", "C++"),
            ("%2Fhome%2Fdeck%2FROMs", "/home/deck/ROMs"),
            ("/home/deck/ROMs", "/home/deck/ROMs"),
            ("Pok%C3%A9mon", "Pokémon"),
            ("%E3%83%9E%E3%83%AA%E3%82%AA", "マリオ"),
            ("Super%5C%20Mario", "Super\\ Mario"),
            ("Super\\Mario", "Super\\Mario"),
            ("100%25", "100%"),
            ("a%26b%3Dc", "a&b=c"),
        ],
    )
    async def test_a_value_arrives_decoded(self, running_host, encoded, decoded):
        status, _, _ = await call_running(running_host, f"/hooks/esde/select?path={encoded}")

        assert status == 200
        assert running_host.hooks.calls == [("select", {"path": decoded})]

    async def test_bytes_that_are_not_utf_8_arrive_as_the_filesystem_spells_them(self, running_host):
        """A Linux file name is bytes; this decoding is ``os.fsdecode``'s, so the name round-trips."""
        await call_running(running_host, "/hooks/esde/select?path=%2Froms%2F%FFgame.sfc")

        ((_, args),) = running_host.hooks.calls
        assert os.fsencode(args["path"]) == b"/roms/\xffgame.sfc"

    async def test_an_empty_value_arrives_empty(self, running_host):
        await call_running(running_host, "/hooks/esde/select?path=")

        assert running_host.hooks.calls == [("select", {"path": ""})]

    async def test_a_hook_with_no_arguments_is_handed_none(self, running_host):
        await call_running(running_host, "/hooks/esde/startup")

        assert running_host.hooks.calls == [("startup", {})]

    @pytest.mark.parametrize("name", HOOK_NAMES)
    async def test_each_name_reaches_its_own(self, running_host, name):
        status, _, _ = await call_running(running_host, f"/hooks/esde/{name}")

        assert status == 200
        assert [called for called, _ in running_host.hooks.calls] == [name]

    async def test_its_answer_is_the_json_body(self, running_host):
        running_host.hooks.answer = {"success": True, "downloaded": False, "name": "Pokémon"}

        status, headers, body = await call_running(running_host, "/hooks/esde/prepare?path=x&escaped=0")

        assert status == 200
        assert headers["content-type"] == "application/json"
        assert headers["server"] == SERVER_IDENTITY
        assert json.loads(body) == {"success": True, "downloaded": False, "name": "Pokémon"}

    async def test_an_endpoint_style_failure_is_a_successful_answer(self, running_host):
        """A refusal is the hook's own answer and rides a 200; only the carriage failing is a 5xx."""
        refusal = {"success": False, "reason": "not_tenders", "message": "Tender does not own this game."}
        running_host.hooks.answer = refusal

        status, _, body = await call_running(running_host, "/hooks/esde/prepare?path=x&escaped=0")

        assert status == 200
        assert json.loads(body) == refusal

    async def test_a_hook_answer_carries_no_cors_header(self, running_host):
        """A script is no browser, and no page is meant to read a hook's answer."""
        _, headers, _ = await call(
            running_host.port, running_host.hook_token, "/hooks/esde/startup", origin="https://steamloopback.host"
        )

        assert "access-control-allow-origin" not in headers


class TestWhatIsNoHook:
    @pytest.mark.parametrize(
        "target",
        [
            "/hooks/esde/launch",
            "/hooks/esde/",
            "/hooks/esde/prepare/more",
            "/hooks/esde/PREPARE",
            "/hooks/esde/%70repare",
            "/hooks/other/prepare",
            "/hooks/",
        ],
    )
    async def test_an_unknown_name_is_a_404_and_calls_nothing(self, running_host, target):
        status, _, body = await call_running(running_host, target)

        assert (status, body) == (404, b"")
        assert running_host.hooks.calls == []

    async def test_a_file_under_the_served_root_is_never_served_on_the_hook_route(self, running_host):
        os.makedirs(os.path.join(running_host.static_root, "hooks", "esde"))
        with open(os.path.join(running_host.static_root, "hooks", "esde", "secret.js"), "w", encoding="utf-8") as f:
            f.write("not for a script")

        status, _, body = await call_running(running_host, "/hooks/esde/secret.js")

        assert status == 404
        assert b"not for a script" not in body


class TestArgumentsThatCannotBeRead:
    async def test_a_name_given_twice_is_refused_and_calls_nothing(self, running_host):
        """Two paths in one call leave the handler to guess which one is the game."""
        status, _, body = await call_running(running_host, "/hooks/esde/select?path=%2Fa&path=%2Fb")

        assert (status, body) == (400, b"")
        assert running_host.hooks.calls == []

    async def test_a_byte_outside_ascii_in_the_address_is_refused(self, running_host):
        """A request target is ASCII; a raw byte there could only be read back as some other name."""
        status, _, _ = await call_running(running_host, "/hooks/esde/select?path=/roms/Pokémon.gb")

        assert status == 400
        assert running_host.hooks.calls == []


class TestWhenTheCarriageFails:
    """Each of these is the transport's own failure, in the transport's shape — never the endpoints'."""

    async def test_a_handler_that_raises_answers_backend_exception(self, running_host):
        running_host.hooks.raises = ValueError("the hook broke")

        status, headers, body = await call_running(running_host, "/hooks/esde/prepare?path=x&escaped=1")
        answer = json.loads(body)

        assert status == 500
        assert headers["content-type"] == "application/json"
        assert (answer["type"], answer["reason"], answer["message"]) == (
            TYPE_ERROR,
            REASON_BACKEND_EXCEPTION,
            "ValueError: the hook broke",
        )
        assert "ValueError: the hook broke" in answer["traceback"]
        assert "success" not in answer

    async def test_a_raised_handler_is_logged_with_its_stack(self, running_host, caplog):
        running_host.hooks.raises = ValueError("the hook broke")

        with caplog.at_level(logging.ERROR, logger="test_host"):
            await call_running(running_host, "/hooks/esde/finish?path=x&escaped=1")

        assert any("finish" in r.getMessage() and "Traceback" in r.getMessage() for r in caplog.records)

    async def test_an_answer_that_is_not_json_answers_backend_exception(self, running_host):
        running_host.hooks.answer = {"not": object()}

        status, _, body = await call_running(running_host, "/hooks/esde/startup")
        answer = json.loads(body)

        assert status == 500
        assert (answer["type"], answer["reason"]) == (TYPE_ERROR, REASON_BACKEND_EXCEPTION)
        assert "not JSON-encodable" in answer["message"]
        assert "success" not in answer

    async def test_an_answer_over_the_cap_is_refused_for_that_call_alone(self, running_host):
        running_host.hooks.answer = {"blob": "x" * (DEFAULT_PAYLOAD_LIMIT + 1)}

        status, _, body = await call_running(running_host, "/hooks/esde/select?path=x")
        answer = json.loads(body)

        assert status == 500
        assert (answer["type"], answer["reason"]) == (TYPE_ERROR, REASON_PAYLOAD_TOO_LARGE)
        assert str(DEFAULT_PAYLOAD_LIMIT) in answer["message"]
        assert len(body) < 1024

    async def test_the_server_answers_the_next_call_as_before(self, running_host):
        running_host.hooks.raises = ValueError("the hook broke")
        await call_running(running_host, "/hooks/esde/startup")
        running_host.hooks.raises = None

        status, _, body = await call_running(running_host, "/hooks/esde/startup")

        assert (status, json.loads(body)) == (200, {"success": True})


class TestTheCapItself:
    """Judged on the encoded bytes, which is what the socket would carry."""

    @staticmethod
    def _routes(answer: object):
        hooks = FakeHooks()
        hooks.answer = answer
        return hooks.routes()

    async def test_an_answer_at_the_cap_is_served(self):
        encoded = json.dumps({"blob": "xxxx"}).encode("utf-8")

        answered = await answer_hook(
            self._routes({"blob": "xxxx"}),
            parse_request_head(b"GET /hooks/esde/startup HTTP/1.1"),
            LOGGER,
            answer_limit=len(encoded),
        )

        assert (answered.status, answered.body) == (200, encoded)

    async def test_one_byte_over_it_is_refused(self):
        encoded = json.dumps({"blob": "xxxx"}).encode("utf-8")

        answered = await answer_hook(
            self._routes({"blob": "xxxx"}),
            parse_request_head(b"GET /hooks/esde/startup HTTP/1.1"),
            LOGGER,
            answer_limit=len(encoded) - 1,
        )

        assert answered.status == 500
        assert json.loads(answered.body)["reason"] == REASON_PAYLOAD_TOO_LARGE


class TestWithNoHandlerWired:
    @pytest.fixture
    async def unwired_host(self, tmp_path):
        static_root = tmp_path / "dist"
        static_root.mkdir()
        server = HostServer(
            dispatcher=CallDispatcher(FakeEndpoints(), LOGGER),
            events=EventSink(LOGGER),
            static_root=str(static_root),
            logger=LOGGER,
            server_identity=SERVER_IDENTITY,
            preferred_port=free_port(),
        )
        await server.start()
        try:
            yield server
        finally:
            await server.stop()

    @pytest.mark.parametrize("name", HOOK_NAMES)
    async def test_every_hook_answers_503(self, unwired_host, name):
        status, _, body = await call(unwired_host.port, unwired_host.hook_token, f"/hooks/esde/{name}?path=x")

        assert (status, body) == (503, b"")

    async def test_a_path_outside_es_des_hooks_is_still_a_404(self, unwired_host):
        status, _, _ = await call(unwired_host.port, unwired_host.hook_token, "/hooks/other/prepare")

        assert status == 404

    async def test_admission_is_judged_first(self, unwired_host):
        status, _, _ = await http_get(unwired_host.port, "/hooks/esde/prepare", origin=None)

        assert status == 401


def _mode(path) -> int:
    return stat.S_IMODE(os.stat(path).st_mode)


class TestTheHookFiles:
    """What a script reads on every call: the port, and the header ``curl -H @file`` sends."""

    def test_it_writes_the_port_and_the_header(self, tmp_path):
        directory = tmp_path / "data" / "esde-hooks"

        HookFiles(str(directory)).write(27740, "the-hook-token")

        assert (directory / HOOK_PORT_FILENAME).read_text(encoding="utf-8") == "27740"
        assert (directory / HOOK_HEADER_FILENAME).read_text(encoding="utf-8") == (
            "X-Tender-Hook-Token: the-hook-token\n"
        )

    def test_the_header_names_the_field_the_access_check_reads(self, tmp_path):
        HookFiles(str(tmp_path)).write(27740, "tok")

        name, _, value = (tmp_path / HOOK_HEADER_FILENAME).read_text(encoding="utf-8").partition(":")
        assert name == HOOK_TOKEN_HEADER
        assert value.strip() == "tok"

    def test_only_its_owner_may_read_any_of_it(self, tmp_path):
        directory = tmp_path / "esde-hooks"

        HookFiles(str(directory)).write(27740, "tok")

        assert _mode(directory) == 0o700
        assert _mode(directory / HOOK_PORT_FILENAME) == 0o600
        assert _mode(directory / HOOK_HEADER_FILENAME) == 0o600

    def test_modes_left_wider_by_an_earlier_hand_are_narrowed(self, tmp_path):
        directory = tmp_path / "esde-hooks"
        directory.mkdir(mode=0o755)
        os.chmod(directory, 0o755)
        for name in (HOOK_PORT_FILENAME, HOOK_HEADER_FILENAME):
            (directory / name).write_text("stale", encoding="utf-8")
            os.chmod(directory / name, 0o644)

        HookFiles(str(directory)).write(27740, "tok")

        assert _mode(directory) == 0o700
        assert _mode(directory / HOOK_PORT_FILENAME) == 0o600
        assert _mode(directory / HOOK_HEADER_FILENAME) == 0o600

    def test_the_modes_hold_whatever_the_umask(self, tmp_path):
        previous = os.umask(0o000)
        try:
            HookFiles(str(tmp_path / "esde-hooks")).write(27740, "tok")
        finally:
            os.umask(previous)

        assert _mode(tmp_path / "esde-hooks" / HOOK_HEADER_FILENAME) == 0o600

    def test_a_second_write_replaces_each_file_whole(self, tmp_path):
        """A reader holding the earlier file reads it to its end, never half of the next one."""
        files = HookFiles(str(tmp_path))
        files.write(27740, "the-first-token")
        with (
            open(tmp_path / HOOK_HEADER_FILENAME, encoding="utf-8") as earlier_header,
            open(tmp_path / HOOK_PORT_FILENAME, encoding="utf-8") as earlier_port,
        ):
            files.write(27741, "the-second-token")

            assert earlier_header.read() == "X-Tender-Hook-Token: the-first-token\n"
            assert earlier_port.read() == "27740"

        assert (tmp_path / HOOK_HEADER_FILENAME).read_text(encoding="utf-8") == (
            "X-Tender-Hook-Token: the-second-token\n"
        )
        assert (tmp_path / HOOK_PORT_FILENAME).read_text(encoding="utf-8") == "27741"

    def test_no_temporary_is_left_beside_them(self, tmp_path):
        files = HookFiles(str(tmp_path))
        files.write(27740, "tok")
        files.write(27741, "tok2")

        assert sorted(os.listdir(tmp_path)) == sorted([HOOK_HEADER_FILENAME, HOOK_PORT_FILENAME])

    def test_the_header_is_in_place_before_the_port_names_this_start(self, tmp_path, monkeypatch):
        """A script that finds this start's port finds this start's token beside it."""
        replaced: list[str] = []
        real_replace = os.replace

        def recording_replace(source, destination):
            replaced.append(os.path.basename(destination))
            real_replace(source, destination)

        monkeypatch.setattr(os, "replace", recording_replace)

        HookFiles(str(tmp_path)).write(27740, "tok")

        assert replaced == [HOOK_HEADER_FILENAME, HOOK_PORT_FILENAME]

    def test_remove_takes_both_away_and_may_be_repeated(self, tmp_path):
        files = HookFiles(str(tmp_path / "esde-hooks"))
        files.write(27740, "tok")

        files.remove()
        files.remove()

        assert os.listdir(tmp_path / "esde-hooks") == []

    def test_remove_with_nothing_written_is_harmless(self, tmp_path):
        HookFiles(str(tmp_path / "never-made")).remove()

        assert not (tmp_path / "never-made").exists()
