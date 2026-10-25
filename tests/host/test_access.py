"""The three checks — that they hold, and that they hold in that order, on every entry point.

The order is tested by constructing requests that fail more than one check at
once and pinning which refusal comes back. A test that only fails one check at a
time would pass under any order at all.
"""

from __future__ import annotations

import pytest

from host.access import HOOK_TOKEN_HEADER, STEAM_UI_ORIGIN, AccessPolicy, check_access, new_token
from host.protocol import ReloadOutlook
from lib.http_messages import parse_request_head
from lib.websocket_frames import OPCODE_CLOSE
from tests.host.conftest import SERVER_IDENTITY
from tests.host.ws_client import WsTestClient, http_get

PORT = 27737
TOKEN = "the-admission-token"
HOOK_TOKEN = "the-hook-token"
POLICY = AccessPolicy(port=PORT, token=TOKEN, hook_token=HOOK_TOKEN)

FILE = "/index.js"
UPGRADE = "/ws"
HOOK = "/hooks/esde/prepare"
ENTRY_POINTS = [FILE, UPGRADE, HOOK]


async def no_reload() -> ReloadOutlook:
    return ReloadOutlook.NO_RELOAD


def head(*, host: str = f"127.0.0.1:{PORT}", origin: str | None = STEAM_UI_ORIGIN, token: str | None = TOKEN):
    """Build a request head for the file route with the three inputs the checks read."""
    return request(FILE, host=host, origin=origin, token=token)


def request(
    path: str,
    *,
    host: str = f"127.0.0.1:{PORT}",
    origin: str | None = STEAM_UI_ORIGIN,
    token: str | None = None,
    hook_token: str | None = None,
    hook_header: str = HOOK_TOKEN_HEADER,
):
    """A request head for *path*: *token* in the query, *hook_token* in its header, each only when given."""
    target = path if token is None else f"{path}{'&' if '?' in path else '?'}token={token}"
    lines = [f"GET {target} HTTP/1.1", f"Host: {host}"]
    if origin is not None:
        lines.append(f"Origin: {origin}")
    if hook_token is not None:
        lines.append(f"{hook_header}: {hook_token}")
    return parse_request_head("\r\n".join(lines).encode("latin-1"))


def is_a_hook(path: str) -> bool:
    return path.startswith("/hooks/")


def offering(path: str, credential: str | None, **kwargs):
    """A request to *path* offering *credential* where that entry point reads its credential."""
    if is_a_hook(path):
        return request(path, hook_token=credential, **kwargs)
    return request(path, token=credential, **kwargs)


def right_credential(path: str) -> str:
    return HOOK_TOKEN if is_a_hook(path) else TOKEN


class TestTheHostCheck:
    @pytest.mark.parametrize("spelling", [f"127.0.0.1:{PORT}", f"localhost:{PORT}"])
    def test_both_spellings_of_this_server_are_accepted(self, spelling):
        assert check_access(head(host=spelling), POLICY).allowed

    @pytest.mark.parametrize(
        "host",
        ["evil.example.com", f"evil.example.com:{PORT}", "127.0.0.1:1", f"127.0.0.2:{PORT}", "", "[::1]:27737"],
    )
    def test_any_other_authority_is_refused(self, host):
        """DNS rebinding makes the request same-origin; the Host field is what gives it away."""
        verdict = check_access(head(host=host), POLICY)

        assert verdict.refused
        assert verdict.status == 421

    def test_the_port_checked_is_the_one_actually_bound(self):
        """A policy built from the port we WANTED would refuse everything after a fallback."""
        fallen_back = AccessPolicy(port=27750, token=TOKEN, hook_token=HOOK_TOKEN)

        assert check_access(head(host="127.0.0.1:27750"), fallen_back).allowed
        assert check_access(head(host=f"127.0.0.1:{PORT}"), fallen_back).refused

    @pytest.mark.parametrize("spelling", [f"127.0.0.1:{PORT}", f"localhost:{PORT}"])
    def test_a_hook_is_judged_by_the_same_authority(self, spelling):
        assert check_access(request(HOOK, host=spelling, hook_token=HOOK_TOKEN), POLICY).allowed
        assert check_access(request(HOOK, host="evil.example.com", hook_token=HOOK_TOKEN), POLICY).status == 421


class TestTheOriginCheck:
    @pytest.mark.parametrize(
        "origin",
        [STEAM_UI_ORIGIN, f"http://127.0.0.1:{PORT}", f"http://localhost:{PORT}"],
    )
    def test_the_allowed_origins_pass(self, origin):
        assert check_access(head(origin=origin), POLICY).allowed

    @pytest.mark.parametrize(
        "origin",
        [
            "https://evil.example.com",
            "http://127.0.0.1:1",
            "https://127.0.0.1:27737",
            "http://[::1]:27737",
            "null",
        ],
    )
    def test_a_foreign_origin_is_refused(self, origin):
        verdict = check_access(head(origin=origin), POLICY)

        assert verdict.refused
        assert verdict.status == 403

    def test_the_refused_origin_is_named_in_the_log_line(self):
        """So the first run answers what Steam's UI actually sends."""
        foreign = "https://evil.example.com"

        verdict = check_access(head(origin=foreign), POLICY)

        assert repr(foreign) in verdict.log_line

    def test_an_absent_origin_passes(self):
        """A browser never omits it; whoever did is not one, and still needs the token."""
        assert check_access(head(origin=None), POLICY).allowed

    def test_an_absent_origin_is_echoed_nowhere(self):
        assert check_access(head(origin=None), POLICY).echo_origin == ""

    def test_an_allowed_origin_is_the_one_echoed(self):
        assert check_access(head(origin=STEAM_UI_ORIGIN), POLICY).echo_origin == STEAM_UI_ORIGIN

    def test_a_hook_without_an_origin_passes(self):
        """What ``curl`` in an ES-DE script sends: no origin at all."""
        assert check_access(request(HOOK, origin=None, hook_token=HOOK_TOKEN), POLICY).allowed

    def test_a_hook_from_a_foreign_origin_is_refused(self):
        verdict = check_access(request(HOOK, origin="https://evil.example.com", hook_token=HOOK_TOKEN), POLICY)

        assert verdict.status == 403


class TestTheTokenCheck:
    def test_the_right_token_passes(self):
        assert check_access(head(token=TOKEN), POLICY).allowed

    @pytest.mark.parametrize("offered", [None, "", "wrong", TOKEN[:-1], TOKEN + "x", TOKEN.upper()])
    def test_anything_else_is_refused(self, offered):
        verdict = check_access(head(token=offered), POLICY)

        assert verdict.refused
        assert verdict.status == 401

    def test_a_missing_token_and_a_wrong_one_read_differently_in_the_log(self):
        assert "no token" in check_access(head(token=None), POLICY).log_line
        assert "wrong token" in check_access(head(token="wrong"), POLICY).log_line

    @pytest.mark.parametrize("offered", ["%C3%A9", "%FF", "%E3%83%9E"])
    def test_a_token_outside_ascii_is_refused_rather_than_raising(self, offered):
        """``secrets.compare_digest`` raises on a non-ASCII ``str``; the check compares bytes."""
        verdict = check_access(head(token=offered), POLICY)

        assert verdict.status == 401
        assert verdict.wrong_token

    def test_only_a_wrong_token_is_flagged_as_one(self):
        """The upgrade answers that one refusal with a close code; every other stays a status."""
        assert check_access(head(token="wrong"), POLICY).wrong_token
        assert not check_access(head(token=None), POLICY).wrong_token
        assert not check_access(head(token=""), POLICY).wrong_token
        assert not check_access(head(token=TOKEN), POLICY).wrong_token
        assert not check_access(head(host="evil.example.com", token="wrong"), POLICY).wrong_token
        assert not check_access(head(origin="https://evil.example.com", token="wrong"), POLICY).wrong_token


class TestTheHookToken:
    """ES-DE's scripts carry a token of their own, in a header, and it opens the hooks and nothing else."""

    @pytest.mark.parametrize("path", [HOOK, "/hooks/esde/startup", "/hooks/esde/not-a-hook", "/hooks/other"])
    def test_it_admits_every_path_under_the_hooks(self, path):
        """Which hook a path names is the route's question, asked after admission."""
        assert check_access(request(path, hook_token=HOOK_TOKEN), POLICY).allowed

    @pytest.mark.parametrize("path", [FILE, UPGRADE, "/", "/hooks"])
    def test_it_admits_nothing_else(self, path):
        verdict = check_access(request(path, hook_token=HOOK_TOKEN), POLICY)

        assert verdict.refused
        assert verdict.status == 401

    @pytest.mark.parametrize("path", [FILE, UPGRADE])
    def test_nor_where_the_admission_token_travels(self, path):
        verdict = check_access(request(path, token=HOOK_TOKEN), POLICY)

        assert verdict.refused
        assert verdict.status == 401

    def test_the_admission_token_admits_no_hook(self):
        verdict = check_access(request(HOOK, token=TOKEN), POLICY)

        assert verdict.refused
        assert verdict.status == 401

    def test_nor_does_it_in_the_hooks_header(self):
        verdict = check_access(request(HOOK, hook_token=TOKEN), POLICY)

        assert verdict.refused
        assert verdict.status == 401

    @pytest.mark.parametrize("offered", [None, "", "wrong", HOOK_TOKEN[:-1], HOOK_TOKEN + "x", HOOK_TOKEN.upper()])
    def test_anything_else_in_the_header_is_refused(self, offered):
        verdict = check_access(request(HOOK, hook_token=offered), POLICY)

        assert verdict.refused
        assert verdict.status == 401

    @pytest.mark.parametrize("offered", ["é", "the-hook-tokén", "\xff"])
    def test_a_header_outside_ascii_is_refused_rather_than_raising(self, offered):
        verdict = check_access(request(HOOK, hook_token=offered), POLICY)

        assert verdict.status == 401

    def test_the_header_is_read_whatever_its_case(self):
        """HTTP field names are case-insensitive, and ``curl -H @file`` sends the file's spelling."""
        assert check_access(request(HOOK, hook_token=HOOK_TOKEN, hook_header="x-tender-hook-token"), POLICY).allowed

    def test_a_missing_hook_token_and_a_wrong_one_read_differently_in_the_log(self):
        assert "no hook token" in check_access(request(HOOK), POLICY).log_line
        assert "wrong hook token" in check_access(request(HOOK, hook_token="wrong"), POLICY).log_line

    def test_the_log_line_never_carries_what_was_offered(self):
        verdict = check_access(request(HOOK, hook_token="a-guess-at-the-token"), POLICY)

        assert "a-guess-at-the-token" not in verdict.log_line

    def test_no_hook_refusal_is_flagged_as_a_stranded_panels(self):
        """Only the upgrade answers a wrong token with more than a status (``.claude/rules/host.md``, rule 3)."""
        assert not check_access(request(HOOK, hook_token="wrong"), POLICY).wrong_token
        assert not check_access(request(HOOK, token="wrong"), POLICY).wrong_token
        assert not check_access(request(HOOK, token=TOKEN), POLICY).wrong_token

    def test_a_policy_without_a_hook_token_admits_no_hook(self):
        policy = AccessPolicy(port=PORT, token=TOKEN, hook_token="")

        assert check_access(request(HOOK, hook_token=""), policy).refused
        assert check_access(request(HOOK, hook_token=HOOK_TOKEN), policy).refused


class TestTheOrder:
    @pytest.mark.parametrize("path", ENTRY_POINTS)
    def test_a_bad_host_is_reported_as_a_bad_host_even_with_no_token(self, path):
        """Otherwise the log says 'wrong token' about a rebinding attempt."""
        verdict = check_access(offering(path, None, host="evil.example.com"), POLICY)

        assert verdict.status == 421

    @pytest.mark.parametrize("path", ENTRY_POINTS)
    def test_a_foreign_origin_outranks_a_bad_token(self, path):
        verdict = check_access(offering(path, "wrong", origin="https://evil.example.com"), POLICY)

        assert verdict.status == 403

    @pytest.mark.parametrize("path", ENTRY_POINTS)
    def test_a_bad_host_outranks_a_foreign_origin(self, path):
        verdict = check_access(
            offering(path, right_credential(path), host="evil.example.com", origin="https://evil.example.com"),
            POLICY,
        )

        assert verdict.status == 421

    @pytest.mark.parametrize("path", ENTRY_POINTS)
    def test_with_all_three_right_it_is_admitted(self, path):
        assert check_access(offering(path, right_credential(path)), POLICY).allowed


class TestTheToken:
    def test_two_tokens_differ(self):
        first, second = new_token(), new_token()

        assert first != second

    def test_a_token_is_long_enough_not_to_be_guessed(self):
        assert len(new_token()) >= 32


async def ask(running_host, path: str, offered: str | None, **kwargs):
    """Request *path* from the running host, offering *offered* where that entry point reads its credential."""
    if is_a_hook(path):
        headers = [] if offered is None else [(HOOK_TOKEN_HEADER, offered)]
        return await http_get(running_host.port, path, headers=headers, **kwargs)
    return await http_get(running_host.port, path, token=offered, **kwargs)


def right_one(running_host, path: str) -> str:
    return running_host.hook_token if is_a_hook(path) else running_host.token


class TestTheChecksOnTheWire:
    """All three entry points — the file route, the upgrade and the hooks — run the same three."""

    @pytest.mark.parametrize("path", ENTRY_POINTS)
    async def test_no_token_is_refused_with_401(self, running_host, path):
        status, headers, _ = await ask(running_host, path, None)

        assert status == 401
        assert headers["server"] == SERVER_IDENTITY

    @pytest.mark.parametrize("path", ENTRY_POINTS)
    async def test_a_wrong_token_is_refused(self, running_host, path):
        status, _, _ = await ask(running_host, path, "not-the-token")

        assert status == 401

    @pytest.mark.parametrize("path", ENTRY_POINTS)
    async def test_a_foreign_host_is_refused(self, running_host, path):
        status, _, _ = await ask(running_host, path, right_one(running_host, path), host="evil.example.com")

        assert status == 421

    @pytest.mark.parametrize("path", ENTRY_POINTS)
    async def test_a_foreign_origin_is_refused(self, running_host, path):
        status, _, _ = await ask(running_host, path, right_one(running_host, path), origin="https://evil.example.com")

        assert status == 403

    @pytest.mark.parametrize("path", [FILE, HOOK])
    async def test_its_own_token_admits_each(self, running_host, path):
        status, _, _ = await ask(running_host, path, right_one(running_host, path), origin=None)

        assert status == 200

    @pytest.mark.parametrize("path", [FILE, UPGRADE])
    async def test_the_hook_token_admits_neither_the_files_nor_the_upgrade(self, running_host, path):
        in_the_header, _, _ = await http_get(
            running_host.port, path, headers=[(HOOK_TOKEN_HEADER, running_host.hook_token)]
        )
        in_the_address, _, _ = await http_get(running_host.port, path, token=running_host.hook_token)

        assert (in_the_header, in_the_address) == (401, 401)

    async def test_the_admission_token_admits_no_hook(self, running_host):
        in_the_address, _, _ = await http_get(running_host.port, HOOK, token=running_host.token)
        in_the_header, _, _ = await http_get(running_host.port, HOOK, headers=[(HOOK_TOKEN_HEADER, running_host.token)])

        assert (in_the_address, in_the_header) == (401, 401)
        assert running_host.hooks.calls == []

    async def test_only_get_is_answered_on_the_hooks(self, running_host):
        status, _, _ = await ask(running_host, HOOK, running_host.hook_token, method="POST")

        assert status == 405
        assert running_host.hooks.calls == []

    async def test_a_refused_request_gets_no_bundle(self, running_host):
        _, _, body = await http_get(running_host.port, "/index.js", token=None)

        assert body == b""

    async def test_an_upgrade_without_a_token_never_becomes_a_connection(self, running_host):
        with pytest.raises(AssertionError, match="refused with 401"):
            await WsTestClient.connect(running_host.port, "")
        # A wrong token is answered with a completed handshake and an immediate
        # close once the backend has looked at Steam (``test_server_stranded_panel.py``)
        # — still never a connection.
        running_host.server.answer_stranded_panels_from(no_reload)
        refused = await WsTestClient.connect(running_host.port, "not-the-token")
        try:
            opcode, _ = await refused.recv_frame()
        finally:
            await refused.close()

        assert opcode == OPCODE_CLOSE
        assert not running_host.server.connected

    async def test_the_token_is_not_served_by_any_route(self, running_host):
        """There is no reader for it: the bundle gets it from the address it loaded from."""
        for path in ("/token", "/index.js", "/ws", "/.token", "/config.json"):
            _, _, body = await http_get(running_host.port, path, token=running_host.token)
            assert running_host.token.encode() not in body

    async def test_neither_token_is_answered_by_a_hook(self, running_host):
        """The hook token reaches a script through its file and nowhere else."""
        for name in ("startup", "prepare", "finish", "select", "token", "hook_token"):
            _, _, body = await ask(running_host, f"/hooks/esde/{name}", running_host.hook_token)
            assert running_host.hook_token.encode() not in body
            assert running_host.token.encode() not in body
