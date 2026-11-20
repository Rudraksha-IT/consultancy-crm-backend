"""How long a deploy's migration may wait for a lock (ticket 14).

The old version keeps running while a deploy migrates. A migration that needs a table the running
app is using waits at most `MIGRATION_LOCK_TIMEOUT_MS`, then fails: the deploy stops before the
switch and the old version keeps running untouched, instead of every request queueing behind it.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from django.conf import settings
from django.db import connection
from django.db.backends.base.base import BaseDatabaseWrapper
from django.db.backends.signals import connection_created

_DISPATCH_UID = "migration_lock_timeout"


def _apply(sender: Any = None, connection: BaseDatabaseWrapper = connection, **_: Any) -> None:  # noqa: ANN401  # a signal receiver's
    """Set the lock timeout on this connection (and on any made while migrating)."""
    timeout = f"{int(settings.MIGRATION_LOCK_TIMEOUT_MS)}ms"
    with connection.cursor() as cursor:
        cursor.execute("SELECT set_config('lock_timeout', %s, false)", [timeout])


@contextmanager
def migration_lock_timeout() -> Iterator[None]:
    """Run the block with the lock timeout set; put the server's default back afterwards.

    After a failure the timeout is left as it is: the deploy's process stops there, and the
    failed transaction would refuse the reset anyway.
    """
    connection_created.connect(_apply, dispatch_uid=_DISPATCH_UID)
    try:
        connection.ensure_connection()
        _apply()
        yield
    finally:
        connection_created.disconnect(dispatch_uid=_DISPATCH_UID)
    with connection.cursor() as cursor:
        cursor.execute("RESET lock_timeout")
