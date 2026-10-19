"""Writing to a feed, and reading it in order."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass

from django.db import connection, transaction

from arkitekt_service.feed.models import FeedEntry, FeedState


class NotInTransaction(RuntimeError):
    """A change was recorded outside the transaction that made it: it could outlive a rollback."""


@dataclass(frozen=True)
class Change:
    """One change to record: what (``feed``), how (``kind``), which object, and whose."""

    feed: str
    kind: str
    object: str
    organization: str
    uuid: uuid.UUID | None = None


@dataclass(frozen=True, order=True)
class Cursor:
    """A position in a feed: after the entry ``id`` of the transaction ``xid``."""

    xid: int = 0
    id: int = 0


@dataclass(frozen=True)
class Entry:
    """An entry as a page hands it out."""

    xid: int
    id: int
    feed: str
    kind: str
    object: str
    uuid: uuid.UUID | None
    organization: str

    @property
    def cursor(self) -> Cursor:
        return Cursor(self.xid, self.id)


@dataclass(frozen=True)
class Page:
    """Entries after a cursor, oldest first.

    ``horizon`` is the oldest transaction still open: nothing at or above it is handed out yet.
    ``current_xid`` is the next transaction id the producer will assign; a reader whose cursor is
    beyond it is following a history the producer no longer has.
    """

    epoch: str
    entries: list[Entry]
    horizon: int
    current_xid: int


def _require_transaction() -> None:
    if not connection.in_atomic_block:
        raise NotInTransaction("Record a change inside the transaction.atomic() that makes it")


def record_change(change: Change) -> None:
    """Record one change. Must run inside the ``transaction.atomic()`` that makes the change."""
    _require_transaction()
    FeedEntry.objects.create(feed=change.feed, kind=change.kind, object=change.object, organization=change.organization, uuid=change.uuid)


def record_changes(changes: Sequence[Change]) -> int:
    """Record many changes in one statement. Must run inside the transaction that makes them."""
    _require_transaction()
    rows = [FeedEntry(feed=c.feed, kind=c.kind, object=c.object, organization=c.organization, uuid=c.uuid) for c in changes]
    FeedEntry.objects.bulk_create(rows, batch_size=5000)
    return len(rows)


_PAGE = f"""
    SELECT e.xid::text, e.id, e.feed, e.kind, e.object, e.uuid, e.organization
    FROM {FeedEntry._meta.db_table} e
    WHERE e.feed = %s AND e.organization = %s
      AND (e.xid, e.id) > (%s::xid8, %s)
      AND e.xid < pg_snapshot_xmin(pg_current_snapshot())
    ORDER BY e.xid, e.id
    LIMIT %s
"""


#: How far a full page is extended to finish the transaction it ends in. A transaction with more
#: entries than this spans pages, and a reader sees it in parts; producers write in batches far
#: below it (mikro: 5000 metrics per call).
MAX_TRANSACTION = 50_000

_REST_OF_TRANSACTION = f"""
    SELECT e.xid::text, e.id, e.feed, e.kind, e.object, e.uuid, e.organization
    FROM {FeedEntry._meta.db_table} e
    WHERE e.feed = %s AND e.organization = %s AND e.xid = %s::xid8 AND e.id > %s
    ORDER BY e.id
    LIMIT %s
"""


def _entries(rows: list[tuple]) -> list[Entry]:
    return [Entry(xid=int(xid), id=id_, feed=f, kind=kind, object=obj, uuid=uid, organization=org) for xid, id_, f, kind, obj, uid, org in rows]


def changes(feed: str, organization: str, after: Cursor | None = None, limit: int = 1000) -> Page:
    """The entries of ``organization`` in ``feed`` after ``after``, of transactions no open one can precede.

    **A page never ends inside a transaction** (up to :data:`MAX_TRANSACTION` entries): a full
    page is extended by the rest of its last transaction's entries, so a reader that applies a
    page as one act sees each of the producer's transactions whole. A page may therefore hold
    more than ``limit`` entries.
    """
    after = after or Cursor()
    with transaction.atomic(), connection.cursor() as cursor:
        # One snapshot for the page and its horizon: REPEATABLE READ is not needed, the page is
        # a single statement and the horizon is read in the same transaction right after.
        cursor.execute(_PAGE, [feed, organization, str(after.xid), after.id, limit])
        entries = _entries(cursor.fetchall())
        if len(entries) == limit:
            # Already below the horizon: the transaction is committed, its rest is all there.
            last = entries[-1]
            cursor.execute(_REST_OF_TRANSACTION, [feed, organization, str(last.xid), last.id, MAX_TRANSACTION])
            entries += _entries(cursor.fetchall())
        cursor.execute("SELECT pg_snapshot_xmin(pg_current_snapshot())::text, pg_snapshot_xmax(pg_current_snapshot())::text")
        horizon, current = cursor.fetchone()
    return Page(epoch=epoch(feed), entries=entries, horizon=int(horizon), current_xid=int(current))


def epoch(feed: str) -> str:
    """Which history ``feed`` is: the cluster, the database, and the feed's own uuid.

    The cluster's system identifier needs ``pg_read_all_settings``-like rights on some setups
    (``pg_control_system()``); where it is refused the database's oid stands alone, which still
    changes on every drop-and-restore.
    """
    state, _ = FeedState.objects.get_or_create(feed=feed)
    with connection.cursor() as cursor:
        cursor.execute("SELECT oid FROM pg_database WHERE datname = current_database()")
        (database,) = cursor.fetchone()
        try:
            with transaction.atomic():
                cursor.execute("SELECT system_identifier::text FROM pg_control_system()")
                (cluster,) = cursor.fetchone()
        except Exception:  # noqa: BLE001  a role without the right reads no cluster id
            cluster = "-"
    return f"{cluster}:{database}:{state.token}"
