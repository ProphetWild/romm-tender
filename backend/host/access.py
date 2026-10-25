"""Who may talk to this server — the three checks, in one order, for every entry point.

Contract: the whole admission decision for an incoming request. The server has
exactly three places a request can enter — the static route, the WebSocket
upgrade and ES-DE's hooks — and each one puts the request through
:func:`check_access`, so there is one order and one set of answers rather than
three that drift.

**The order is Host, then Origin, then Token, and it is not arbitrary.** Host
first so a refusal is logged as the thing it is: a request that reached us under
a name we do not answer to is a rebinding attempt, and logging it as "wrong
token" would bury that. Origin second, because it is a statement about *who
asked*, which is worth having in the log before the credential decides anything.
Token last, because it is the only one of the three that actually authorises.

Two of the three are defence in depth rather than the gate:

- The **Host** check closes DNS rebinding. An attacker who resolves their own
  name to 127.0.0.1 makes the request same-origin, so no CORS check fires, and a
  plain ``GET`` carries no ``Origin`` at all. Followed through, the attack buys
  nothing here anyway because nothing is served and no call is carried without
  the token — the check is in place so nobody has to reconstruct that reasoning
  again.
- The **Origin** check is protection against a foreign web page, **not a
  login**. An absent origin is allowed through: a browser never omits it on a
  WebSocket handshake, so whoever omitted it is not a browser, and they still
  need the token. A foreign origin is refused *and written to the log with its
  value*, which is what answers "what origin does Steam's UI actually send"
  on the first run instead of by a measurement on the device.

**Two tokens, and each opens only its own routes.** Both are per-process random
values that live only in memory, and neither admits the other's routes: which
token a request is judged by follows from its path alone.

- The **admission token** opens the files and the upgrade. No route and no file
  hands it out, and there is no reader for one: the bundle receives it in the
  address it is loaded from, and whatever injects that address runs in this
  process. It travels as part of the address and never as a header — a module
  load cannot set a header of its own, and any header of ours would force a CORS
  preflight on every request.
- The **hook token** opens everything under :data:`HOOKS_PREFIX` and nothing
  else. ES-DE runs in a flatpak that does not share this process's memory, so
  the token is handed over in a file (``host.hooks.HookFiles``), and the scripts
  send it as the :data:`HOOK_TOKEN_HEADER` header with ``curl -H @file``. In a
  header it sits in no request line, which an unreadable request is logged
  with, and read from a file it is on no command line; a script is no browser,
  so the header costs no preflight. A browser page that tried to send it would
  be stopped by exactly that preflight, which this server refuses.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from lib.http_messages import RequestHead

# Steam's own UI. The panel's code runs in the SharedJSContext window, whose origin
# this is; it is the one origin that is not ours.
STEAM_UI_ORIGIN = "https://steamloopback.host"

# The query parameter the admission token travels in, and the one the caller's
# session identity travels in. Both are address, never header — see the module
# docstring.
TOKEN_PARAM = "token"
SESSION_PARAM = "session"

# Everything the hook token admits, and the header it travels in.
HOOKS_PREFIX = "/hooks/"
HOOK_TOKEN_HEADER = "X-Tender-Hook-Token"

_HOST_MISMATCH = 421
_ORIGIN_REFUSED = 403
_TOKEN_REFUSED = 401


@dataclass(frozen=True)
class AccessPolicy:
    """What this process will answer to, and what it will answer for.

    Both halves are built from the port the server **actually bound**, never
    from the port it asked for: the bind falls back when the preferred port is
    taken, and a policy built from the wish would refuse every request the
    moment it did.

    An empty *hook_token* admits no hook at all.
    """

    port: int
    token: str
    hook_token: str

    @property
    def allowed_hosts(self) -> frozenset[str]:
        """The two spellings of this server's own authority."""
        return frozenset({f"127.0.0.1:{self.port}", f"localhost:{self.port}"})

    @property
    def allowed_origins(self) -> frozenset[str]:
        """Steam's UI plus our own address — which a browser counts as two origins.

        ``127.0.0.1`` and ``localhost`` are different origins to a browser even
        though they are one address to the kernel, so both are listed. ``[::1]``
        is deliberately absent: this server binds no IPv6 address, so nothing
        can arrive from one.
        """
        return frozenset(
            {
                STEAM_UI_ORIGIN,
                f"http://127.0.0.1:{self.port}",
                f"http://localhost:{self.port}",
            }
        )


@dataclass(frozen=True)
class AccessVerdict:
    """The admission decision, plus what to say about it.

    ``echo_origin`` is the origin a CORS header must name when the request is
    allowed and carried one. It is empty for a request with no origin, which is
    a request no CORS header belongs on.

    ``wrong_token`` is set only where Host and Origin passed and an admission
    token was offered that is not this process's — never on the hooks. It is the
    one refusal the upgrade answers differently (``host.server``): a panel
    carries the token of the backend that loaded it, so such a request is the
    shape of a panel another backend process left in Steam. A request with no
    token at all is not — a panel never opens a connection without one
    (``frontend/src/api/hostSocket.ts``).
    """

    allowed: bool
    status: int
    log_line: str
    echo_origin: str
    wrong_token: bool = False

    @property
    def refused(self) -> bool:
        """Was this request turned away?"""
        return not self.allowed


def is_hook_path(path: str) -> bool:
    """Is *path* one the hook token is judged for, rather than the admission token?

    Asked of the path exactly as the request line spelled it, by both the
    admission and the routing, so the two cannot read one path two ways.
    """
    return path.startswith(HOOKS_PREFIX)


def check_access(head: RequestHead, policy: AccessPolicy) -> AccessVerdict:
    """Put *head* through Host, Origin and Token, in that order.

    Returns the first refusal, or an allowing verdict carrying the origin to
    echo. The log line is written by the caller rather than here — this function
    performs no I/O — but its wording is decided here so every route describes
    the same refusal the same way.
    """
    request_host = head.header("host")
    if request_host not in policy.allowed_hosts:
        return AccessVerdict(
            allowed=False,
            status=_HOST_MISMATCH,
            log_line=f"refused {head.method} {head.path}: host {request_host!r} is not this server",
            echo_origin="",
        )

    origin = head.header("origin")
    if origin and origin not in policy.allowed_origins:
        return AccessVerdict(
            allowed=False,
            status=_ORIGIN_REFUSED,
            log_line=f"refused {head.method} {head.path}: origin {origin!r} is not allowed",
            echo_origin="",
        )

    if is_hook_path(head.path):
        offered = head.header(HOOK_TOKEN_HEADER)
        if not _is(offered, policy.hook_token):
            return AccessVerdict(
                allowed=False,
                status=_TOKEN_REFUSED,
                log_line=f"refused {head.method} {head.path}: {'no hook token' if not offered else 'wrong hook token'}",
                echo_origin="",
            )
        return AccessVerdict(allowed=True, status=0, log_line="", echo_origin=origin)

    offered = head.query.get(TOKEN_PARAM, "")
    if not _is(offered, policy.token):
        return AccessVerdict(
            allowed=False,
            status=_TOKEN_REFUSED,
            log_line=f"refused {head.method} {head.path}: {'no token' if not offered else 'wrong token'}",
            echo_origin="",
            wrong_token=bool(offered),
        )

    return AccessVerdict(allowed=True, status=0, log_line="", echo_origin=origin)


def _is(offered: str, token: str) -> bool:
    """Is *offered* this process's *token*? Never for an empty offer, whatever the token.

    Compared as bytes: ``secrets.compare_digest`` raises on a ``str`` holding
    anything outside ASCII, and an offer is whatever the caller sent.
    """
    if not offered:
        return False
    return secrets.compare_digest(offered.encode("utf-8", "surrogatepass"), token.encode("utf-8"))


def new_token() -> str:
    """A fresh token for one process lifetime — the admission token and the hook token alike."""
    return secrets.token_urlsafe(32)
