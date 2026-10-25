"""ES-DE's hooks — the files a script reads to call this backend, and what one call answers.

Contract: the third entry point's own half. Admission is not decided here:
:func:`host.access.check_access` judges a hook request as it judges the other
two, against the hook token. What is decided here is everything after it —
which hook a path names, what its arguments are, and what goes back — and the
two files ES-DE's scripts find this backend by.

**A hook is a ``GET`` whose arguments are its query.** No body is read, as on
every other route. The handler is a mapping from hook name to a callable the
entry point builds; the host imports nothing that answers one, and knows of the
names only what that mapping holds.

**The two failure shapes stay apart here as on the WebSocket**
(``.claude/rules/host.md``, rule 1). A hook's answer, a refusal in the
endpoints' ``{success, reason, message}`` shape included, is a successful
carriage: ``200`` and the answer as JSON. A failure of the carriage itself — the
handler raised, or its answer cannot be encoded or is over the cap — is a
``500`` carrying the transport's ``{type, reason, message, traceback?}``, in the
vocabulary of ``host.protocol``. A path that names no hook is a bare ``404``, a
query that cannot be read a bare ``400``, and a backend with no hook service
wired a bare ``503``.

**The cap is the dispatcher's**, judged on the encoded bytes, and a breach is
that one call's problem (rule 5). There is no frame to announce a length here:
the answer is built by this process, and the cap keeps one oversized answer from
being written at all.

**The files are written once the port is bound and taken away at shutdown**,
beside the port file (``host.runtime``), so finding them means the port answers.
They live in the data root because ES-DE runs in RetroDECK's flatpak, which
shares the home folder (``--filesystem=host``) but not ``/run``, where the port
file lies (``net.retrodeck.retrodeck.yml``).
"""

from __future__ import annotations

import contextlib
import json
import os
import traceback
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol
from urllib.parse import parse_qs, urlsplit

from host.access import HOOK_TOKEN_HEADER, HOOKS_PREFIX
from host.dispatch import DEFAULT_PAYLOAD_LIMIT
from host.protocol import REASON_BACKEND_EXCEPTION, REASON_PAYLOAD_TOO_LARGE, TYPE_ERROR

if TYPE_CHECKING:
    import logging
    from collections.abc import Mapping

    from lib.http_messages import RequestHead

# Where ES-DE's hooks are. A path under :data:`host.access.HOOKS_PREFIX` but not
# under this names no hook.
ESDE_HOOKS_PREFIX = f"{HOOKS_PREFIX}esde/"

# The directory the hook files are written into, under the data root, and the two
# files in it.
HOOK_FILES_DIRNAME = "esde-hooks"
HOOK_PORT_FILENAME = "port"
HOOK_HEADER_FILENAME = "header"

_OK = 200
_UNREADABLE = 400
_NO_SUCH_HOOK = 404
_CARRIAGE_FAILED = 500
_NOT_WIRED = 503


class HookFn(Protocol):
    """Answers one hook call from its decoded query arguments, with a JSON-encodable value."""

    async def __call__(self, args: Mapping[str, str], /) -> object: ...


@dataclass(frozen=True)
class HookAnswer:
    """What a hook request is answered with: a status, and a JSON body or none."""

    status: int
    body: bytes = b""


class HookArgumentError(ValueError):
    """Raised when a hook's query cannot be read as one value per name."""


async def answer_hook(
    routes: Mapping[str, HookFn] | None,
    head: RequestHead,
    logger: logging.Logger,
    *,
    answer_limit: int = DEFAULT_PAYLOAD_LIMIT,
) -> HookAnswer:
    """Answer the admitted hook request *head* from *routes*.

    *routes* maps each hook name to what answers it; ``None`` means no hook
    service is wired, and every hook answers ``503``. Never raises for anything
    the handler did: a raise, an answer that cannot be encoded and one over
    *answer_limit* bytes each come back as a ``500`` for this request alone.
    ``CancelledError`` passes through — there is nobody left to answer.
    """
    if not head.path.startswith(ESDE_HOOKS_PREFIX):
        return HookAnswer(_NO_SUCH_HOOK)
    if routes is None:
        return HookAnswer(_NOT_WIRED)
    name = head.path.removeprefix(ESDE_HOOKS_PREFIX)
    hook = routes.get(name)
    if hook is None:
        return HookAnswer(_NO_SUCH_HOOK)

    try:
        args = hook_arguments(head.target)
    except HookArgumentError as exc:
        logger.warning(f"host: hook {name}: {exc}")
        return HookAnswer(_UNREADABLE)

    try:
        answer = await hook(args)
    except Exception as exc:
        stack = traceback.format_exc()
        logger.error(f"host: hook {name} raised {type(exc).__name__}: {exc}\n{stack}")
        return _carriage_failed(REASON_BACKEND_EXCEPTION, f"{type(exc).__name__}: {exc}", stack)

    try:
        body = json.dumps(answer).encode("utf-8")
    except (TypeError, ValueError) as exc:
        logger.error(f"host: the answer from hook {name} is not encodable: {exc}")
        return _carriage_failed(REASON_BACKEND_EXCEPTION, f"answer is not JSON-encodable: {exc}")

    if len(body) > answer_limit:
        logger.warning(f"host: the answer from hook {name} is {len(body)} bytes, over the {answer_limit} cap")
        return _carriage_failed(
            REASON_PAYLOAD_TOO_LARGE, f"answer is {len(body)} bytes, over the {answer_limit}-byte limit"
        )
    return HookAnswer(_OK, body)


def hook_arguments(target: str) -> dict[str, str]:
    """The query of *target* as one decoded value per name.

    Percent escapes are decoded as ``os.fsdecode`` decodes a file name: UTF-8,
    with a byte that is not UTF-8 kept as a surrogate. A Linux path is bytes, so
    a ROM whose name is not UTF-8 arrives as the same string the filesystem
    calls answer for it, rather than with its bytes replaced. Raises
    :class:`HookArgumentError` for a name given twice, which would leave the
    handler to guess which value is meant, and for a byte outside ASCII sent
    unescaped, which a request target may not carry and which could only be read
    back as some other name.
    """
    query = urlsplit(target).query
    if not query.isascii():
        raise HookArgumentError("the query holds a byte outside ASCII that was not percent-encoded")
    decoded = parse_qs(query, keep_blank_values=True, encoding="utf-8", errors="surrogateescape")
    repeated = sorted(name for name, values in decoded.items() if len(values) > 1)
    if repeated:
        raise HookArgumentError(f"given more than once: {', '.join(repeated)}")
    return {name: values[0] for name, values in decoded.items()}


def _carriage_failed(reason: str, message: str, stack: str | None = None) -> HookAnswer:
    """A ``500`` carrying the transport's failure shape — never the endpoints' ``success`` key."""
    payload: dict[str, str] = {"type": TYPE_ERROR, "reason": reason, "message": message}
    if stack is not None:
        payload["traceback"] = stack
    return HookAnswer(_CARRIAGE_FAILED, json.dumps(payload).encode("utf-8"))


class HookFiles:
    """The two files ES-DE's scripts read on every call: this backend's port, and the header that admits them.

    ``header`` holds exactly the one line ``curl -H @file`` sends. Only this
    user may read either: the directory is ``0700`` and each file ``0600``,
    set explicitly rather than left to the umask.
    """

    def __init__(self, directory: str) -> None:
        self._directory = directory

    @property
    def directory(self) -> str:
        """Where the two files live."""
        return self._directory

    def write(self, port: int, hook_token: str) -> None:
        """Record *port* and *hook_token*, replacing whatever an earlier start left.

        Each file is written through a temporary beside it and renamed over the
        old one, so a reader sees one start's file whole and never half of the
        next. The header goes first: a script that finds this start's port
        finds this start's token already beside it.
        """
        os.makedirs(self._directory, mode=0o700, exist_ok=True)
        os.chmod(self._directory, 0o700)
        self._replace(HOOK_HEADER_FILENAME, f"{HOOK_TOKEN_HEADER}: {hook_token}\n")
        self._replace(HOOK_PORT_FILENAME, str(port))

    def remove(self) -> None:
        """Take both files away. Idempotent; the directory stays."""
        for name in (HOOK_PORT_FILENAME, HOOK_HEADER_FILENAME):
            with contextlib.suppress(OSError):
                os.unlink(os.path.join(self._directory, name))

    def _replace(self, name: str, content: str) -> None:
        final = os.path.join(self._directory, name)
        temporary = os.path.join(self._directory, f".{name}.{os.getpid()}.tmp")
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(content)
        except BaseException:
            with contextlib.suppress(OSError):
                os.unlink(temporary)
            raise
        os.replace(temporary, final)
