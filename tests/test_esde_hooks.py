"""ES-DE's hooks as ``main`` answers them: one method per hook, refusals in the endpoints' shape, bugs left to the host.

The service behind them is a stand-in that records what it is asked or raises
what a case hands it, which puts the exception exactly where the hook service
would raise it. The last class runs the routes through the host's own
``answer_hook``, so the two halves of the failure rule — a refusal is an
answer, anything else the carriage's failure — are seen as a script meets them.
"""

from __future__ import annotations

import dataclasses
import json
import logging
from typing import Any

import pytest

from domain.refusal import DomainRefused
from host.dispatch import route_names
from host.hooks import answer_hook
from host.protocol import REASON_BACKEND_EXCEPTION, TYPE_ERROR
from lib.errors import Refused, RommConnectionError, RommNotFoundError, classify_error
from lib.http_messages import parse_request_head
from lib.partial_failure import PartialFailure
from main import Endpoints, EsdeHooks, esde_hook_routes

LOGGER = logging.getLogger("test_esde_hooks")

HOOK_NAMES = ("startup", "prepare", "finish", "select")

# One set of arguments each hook takes, as the script sends them.
ARGUMENTS = {
    "startup": {},
    "prepare": {"path": "/roms/snes/a.sfc", "escaped": "1"},
    "finish": {"path": "/roms/snes/a.sfc", "escaped": "1"},
    "select": {"path": "/roms/snes/a.sfc"},
}


class _RecordingHookService:
    """The hook service in shape: records each call and answers ``answer``."""

    def __init__(self, answer: Any = None) -> None:
        self.calls: list[tuple[Any, ...]] = []
        self.answer = {"success": True} if answer is None else answer

    async def startup(self) -> Any:
        self.calls.append(("startup",))
        return self.answer

    async def prepare(self, path: str, escaped: bool) -> Any:
        self.calls.append(("prepare", path, escaped))
        return self.answer

    async def finish(self, path: str, escaped: bool) -> Any:
        self.calls.append(("finish", path, escaped))
        return self.answer

    async def select(self, path: str) -> Any:
        self.calls.append(("select", path))
        return self.answer


class _RaisingHookService:
    """The hook service in shape, raising *exc* from every hook."""

    def __init__(self, exc: BaseException) -> None:
        self._exc = exc

    def __getattr__(self, name: str) -> Any:
        async def _raise(*_args: Any) -> Any:
            raise self._exc

        return _raise


def _routes(service: Any):
    return esde_hook_routes(EsdeHooks(service))


class TestEachNameReachesItsMethod:
    def test_the_routes_are_the_four_hooks(self):
        assert set(_routes(_RecordingHookService())) == set(HOOK_NAMES)

    @pytest.mark.parametrize(
        ("name", "args", "asked"),
        [
            ("startup", {}, ("startup",)),
            ("prepare", {"path": "/roms/snes/a b.sfc", "escaped": "1"}, ("prepare", "/roms/snes/a b.sfc", True)),
            ("prepare", {"path": "/roms/snes/a.sfc", "escaped": "0"}, ("prepare", "/roms/snes/a.sfc", False)),
            ("finish", {"path": "/roms/snes/a\\ b.sfc", "escaped": "1"}, ("finish", "/roms/snes/a\\ b.sfc", True)),
            ("finish", {"path": "/roms/snes/a.sfc", "escaped": "0"}, ("finish", "/roms/snes/a.sfc", False)),
            ("select", {"path": "/roms/snes/a.sfc"}, ("select", "/roms/snes/a.sfc")),
        ],
    )
    async def test_each_hook_asks_its_own_method_with_its_arguments(self, name, args, asked):
        service = _RecordingHookService()

        await _routes(service)[name](args)

        assert service.calls == [asked]

    @pytest.mark.parametrize("name", HOOK_NAMES)
    async def test_the_services_answer_is_the_hooks(self, name):
        answer = {"success": True, "downloaded": True}

        assert await _routes(_RecordingHookService(answer))[name](ARGUMENTS[name]) == answer

    @pytest.mark.parametrize("name", ["prepare", "finish", "select"])
    async def test_a_path_not_given_is_no_answer_but_a_bug(self, name):
        """The scripts are ours; a call without its path is a script that is wrong."""
        args = {key: value for key, value in ARGUMENTS[name].items() if key != "path"}

        with pytest.raises(KeyError):
            await _routes(_RecordingHookService())[name](args)

    @pytest.mark.parametrize("name", ["prepare", "finish"])
    @pytest.mark.parametrize("escaped", [None, "", "true", "yes", "2"])
    async def test_escaped_is_one_or_zero_and_nothing_else(self, name, escaped):
        args = {"path": "/roms/snes/a.sfc"} if escaped is None else {"path": "/roms/snes/a.sfc", "escaped": escaped}

        with pytest.raises((KeyError, ValueError)):
            await _routes(_RecordingHookService())[name](args)

    def test_no_hook_is_reachable_over_the_websocket(self):
        """Every public method of ``Endpoints`` carries ``@route``; the hooks live apart for exactly that reason."""
        assert route_names(EsdeHooks) == frozenset()
        assert not set(HOOK_NAMES) & route_names(Endpoints)


@dataclasses.dataclass(frozen=True)
class _DownloadedSome(PartialFailure):
    downloaded: int


class TestARefusalIsTheHooksAnswer:
    @pytest.mark.parametrize("name", HOOK_NAMES)
    async def test_a_raised_refusal_answers_the_failure_shape_with_its_details(self, name):
        refusal = Refused("not_tenders", "Tender does not own this game.", path="/roms/a.sfc")

        answer = await _routes(_RaisingHookService(refusal))[name](ARGUMENTS[name])

        assert answer == {
            "success": False,
            "reason": "not_tenders",
            "message": "Tender does not own this game.",
            "path": "/roms/a.sfc",
        }

    @pytest.mark.parametrize("name", HOOK_NAMES)
    async def test_a_refusal_the_domain_raised_answers_the_failure_shape(self, name):
        answer = await _routes(_RaisingHookService(DomainRefused("target_occupied", "Taken.")))[name](ARGUMENTS[name])

        assert answer == {"success": False, "reason": "target_occupied", "message": "Taken."}

    @pytest.mark.parametrize("name", HOOK_NAMES)
    @pytest.mark.parametrize("exc", [RommConnectionError("refused"), RommNotFoundError("gone")])
    async def test_a_romm_error_answers_classify_errors_verdict(self, name, exc):
        answer = await _routes(_RaisingHookService(exc))[name](ARGUMENTS[name])

        reason, message = classify_error(exc)
        assert answer == {"success": False, "reason": reason, "message": message}

    async def test_a_returned_partial_failure_is_serialized(self):
        result = _DownloadedSome(reason="unknown", message="The download stopped.", downloaded=1)

        answer = await _routes(_RecordingHookService(result))["prepare"](ARGUMENTS["prepare"])

        assert answer == {"success": False, "reason": "unknown", "message": "The download stopped.", "downloaded": 1}

    @pytest.mark.parametrize("name", HOOK_NAMES)
    @pytest.mark.parametrize("exc", [RuntimeError("a bug"), OSError("disk"), ConnectionError("reset")])
    async def test_anything_else_is_raised_for_the_host(self, name, exc):
        with pytest.raises(type(exc)):
            await _routes(_RaisingHookService(exc))[name](ARGUMENTS[name])


class TestAsAScriptMeetsThem:
    """Through the host's own ``answer_hook``: the refusal rides a 200, a bug is the carriage's 500."""

    @staticmethod
    async def _ask(service: Any, target: bytes):
        return await answer_hook(_routes(service), parse_request_head(b"GET " + target + b" HTTP/1.1"), LOGGER)

    async def test_a_refusal_is_a_successful_carriage(self):
        answered = await self._ask(
            _RaisingHookService(Refused("not_tenders", "Not ours.")), b"/hooks/esde/prepare?path=x&escaped=1"
        )

        assert answered.status == 200
        assert json.loads(answered.body) == {"success": False, "reason": "not_tenders", "message": "Not ours."}

    async def test_a_bug_is_the_carriages_failure_and_never_the_endpoints_shape(self):
        answered = await self._ask(_RaisingHookService(RuntimeError("a bug")), b"/hooks/esde/select?path=x")
        answer = json.loads(answered.body)

        assert answered.status == 500
        assert (answer["type"], answer["reason"], answer["message"]) == (
            TYPE_ERROR,
            REASON_BACKEND_EXCEPTION,
            "RuntimeError: a bug",
        )
        assert "success" not in answer

    async def test_a_missing_argument_is_the_carriages_failure_too(self):
        answered = await self._ask(_RecordingHookService(), b"/hooks/esde/select")

        assert answered.status == 500
        assert json.loads(answered.body)["reason"] == REASON_BACKEND_EXCEPTION
