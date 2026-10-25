"""The process that hosts this backend — the port, the protocol and the lifetime.

Contract: everything that used to be a plugin loader's job. It holds the
single-instance lock, brings the backend up in an order that makes its readiness
signal true, binds a loopback port, serves the panel bundle, carries calls and
events over one WebSocket, and answers ES-DE's hook scripts.

**Standard library only.** Nothing here imports ``services``, ``adapters``,
``domain`` or ``bootstrap``, and nothing outside ``main.py`` imports this
package — both directions are contracts in ``.importlinter``. The second half is
the one that rots without a check: the first service that wants to send an event
would reach in here for the sink, and the composition root would stop being the
only place that knows a transport exists.

The names below are this package's whole public surface; ``main.py`` imports
them from here and never from a submodule.
"""

from host.access import new_token
from host.dispatch import CallDispatcher
from host.events import EventSink
from host.hooks import HOOK_FILES_DIRNAME, HookFn
from host.inject import InjectionSetup
from host.logging_setup import configure_logging, configure_stderr_logging
from host.route import route
from host.runtime import AlreadyRunningError, BackendBuild, run_backend
from host.single_instance import LOCK_FILENAME, PORT_FILENAME
from host.status import HostStatus

__all__ = [
    "HOOK_FILES_DIRNAME",
    "LOCK_FILENAME",
    "PORT_FILENAME",
    "AlreadyRunningError",
    "BackendBuild",
    "CallDispatcher",
    "EventSink",
    "HookFn",
    "HostStatus",
    "InjectionSetup",
    "configure_logging",
    "configure_stderr_logging",
    "new_token",
    "route",
    "run_backend",
]
