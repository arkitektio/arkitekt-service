"""Applying another service's feed: each page in one transaction with the cursor.

    outcome = apply_pages(reader, organization="lab", apply=apply_page, budget_s=20)

``apply`` gets one page's entries and writes what they mean; the cursor moves in the same
transaction, so a page is applied once or not at all. Pages are fetched outside any transaction
(an HTTP round trip must not hold one open), then applied under a transaction-scoped advisory
lock on ``(producer, feed, organization)``: a second run that finds it held returns at once —
the holder reads until the feed is empty. If the cursor moved between fetching and applying,
the page is fetched again.

Reading stops and **stays stopped** (``FeedCursor.halted``, until :func:`resume`) when:

- the producer's epoch is not the one the cursor follows (a restore: another history),
- the producer's ``current_xid`` is below the cursor (a counter that went back),
- ``apply`` raises :class:`Poison` — an entry that can never be applied as it is.

Any other error from ``apply`` rolls the page back and is raised: the cursor stays, the run
fails where it can be seen, and the next run tries the same page again. Nothing is ever skipped.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum

from django.db import connection, transaction

from arkitekt_service.feed.api import Cursor
from arkitekt_service.feed.models import FeedCursor
from arkitekt_service.feed.reader import FeedReader, RemoteEntry, RemotePage

logger = logging.getLogger(__name__)


#: How a run ended.
class Ended(StrEnum):
    CAUGHT_UP = "CAUGHT_UP"  # read until a page came back empty
    BUDGET = "BUDGET"  # stopped for time; the next run goes on
    BUSY = "BUSY"  # another run holds the lock and is reading
    HALTED = "HALTED"  # stopped for good, see FeedCursor.halted


class Poison(Exception):
    """An entry that can never be applied as it is. Halts the feed with this message."""


@dataclass(frozen=True)
class Outcome:
    ended: Ended
    applied: int
    pages: int
    cursor: Cursor
    halted: str | None = None

    def report(self) -> dict[str, str | int | None]:
        """As a hook action returns it."""
        return {"ended": self.ended.value, "applied": self.applied, "pages": self.pages, "xid": str(self.cursor.xid), "id": self.cursor.id, "halted": self.halted}


def _cursor_row(reader: FeedReader, organization: str) -> FeedCursor:
    row, _ = FeedCursor.objects.get_or_create(producer=reader.producer, feed=reader.feed, organization=organization)
    return row


def _locked(reader: FeedReader, organization: str) -> bool:
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_try_advisory_xact_lock(hashtextextended(%s, 0))", [f"arkitekt-feed:{reader.producer}:{reader.feed}:{organization}"])
        (got,) = cursor.fetchone()
    return bool(got)


def _halt(reader: FeedReader, organization: str, why: str) -> None:
    logger.error("Halted reading %s from %s for %s: %s", reader.feed, reader.producer, organization, why)
    FeedCursor.objects.filter(producer=reader.producer, feed=reader.feed, organization=organization).update(halted=why)


def _refusal(row: FeedCursor, page: RemotePage) -> str | None:
    """Why ``page`` is not a continuation of the history ``row`` follows, or None."""
    if row.epoch is not None and row.epoch != page.epoch:
        return f"The producer's epoch changed from {row.epoch} to {page.epoch}: it was restored, or is another database"
    if page.current_xid < row.xid:
        return f"The producer's transaction counter ({page.current_xid}) is below this cursor ({row.xid}): it went back"
    return None


def apply_pages(reader: FeedReader, organization: str, apply: Callable[[list[RemoteEntry]], None], *, budget_s: float = 20.0, limit: int = 1000) -> Outcome:
    """Apply ``organization``'s entries after its cursor, page by page, until caught up or out of time."""
    deadline = time.monotonic() + budget_s
    applied = pages = 0
    row = _cursor_row(reader, organization)
    while True:
        if row.halted:
            return Outcome(Ended.HALTED, applied, pages, Cursor(row.xid, row.entry), row.halted)
        fetched_at = Cursor(row.xid, row.entry)
        page = reader.page(organization, fetched_at, limit)

        with transaction.atomic():
            if not _locked(reader, organization):
                return Outcome(Ended.BUSY, applied, pages, fetched_at)
            row = FeedCursor.objects.select_for_update().get(pk=row.pk)
            if row.halted:
                continue
            if Cursor(row.xid, row.entry) != fetched_at:
                continue  # another run moved it between our fetch and our lock: fetch again
            refusal = _refusal(row, page)
            if refusal is None and page.entries:
                try:
                    with transaction.atomic():
                        apply(page.entries)
                except Poison as error:
                    refusal = f"Entry after {fetched_at.xid}:{fetched_at.id} cannot be applied: {error}"
            if refusal is None:
                row.epoch = page.epoch
                if page.entries:
                    last = page.entries[-1].cursor
                    row.xid, row.entry = last.xid, last.id
                row.save(update_fields=["epoch", "xid", "entry", "updated_at"])
        if refusal is not None:
            _halt(reader, organization, refusal)
            row.refresh_from_db()
            continue
        if not page.entries:
            return Outcome(Ended.CAUGHT_UP, applied, pages, Cursor(row.xid, row.entry))
        applied += len(page.entries)
        pages += 1
        if time.monotonic() >= deadline:
            return Outcome(Ended.BUDGET, applied, pages, Cursor(row.xid, row.entry))


def resume(producer: str, feed: str, organization: str, *, adopt_epoch: bool = False) -> None:
    """Let a halted cursor read again. ``adopt_epoch`` forgets the epoch it followed — for after a
    restore someone has decided to accept (the cursor's position is kept: reset it by hand too
    when the restored history should be read from the start)."""
    updates: dict[str, str | None] = {"halted": None}
    if adopt_epoch:
        updates["epoch"] = None
    FeedCursor.objects.filter(producer=producer, feed=feed, organization=organization).update(**updates)
