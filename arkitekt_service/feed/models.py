"""The feed's tables: the producer's entries and epoch, and a reader's cursors."""

from __future__ import annotations

import uuid

from django.db import models
from django.db.models.functions import Now


class Xid8Field(models.Field):
    """A Postgres ``xid8``: a transaction id, 64 bits, never wrapping. Read back as an ``int``."""

    description = "A transaction id (xid8)"

    def db_type(self, connection: object) -> str:
        return "xid8"

    def from_db_value(self, value: object, expression: object, connection: object) -> int | None:
        return None if value is None else int(str(value))

    def to_python(self, value: object) -> int | None:
        return None if value is None else int(str(value))

    def get_prep_value(self, value: object) -> str | None:
        # Sent as text: Postgres has no cast from bigint to xid8, but takes the literal.
        return None if value is None else str(int(str(value)))


class CurrentXid(models.Func):
    """``pg_current_xact_id()``: the id of the transaction that writes the row."""

    function = "pg_current_xact_id"
    template = "%(function)s()"
    output_field = Xid8Field()


class FeedEntry(models.Model):
    """One change, written in the transaction that made it."""

    class Kind(models.TextChoices):
        CREATED = "CREATED"
        UPDATED = "UPDATED"
        DELETED = "DELETED"

    feed = models.CharField(max_length=200, help_text="What changed, as a structure identifier: `@mikro/metric`.")
    organization = models.CharField(max_length=200, help_text="The identifier of the organization the object belongs to (`<iss>#<org>`); a reader reads one organization's entries.")
    kind = models.CharField(max_length=16, choices=Kind.choices)
    object = models.CharField(max_length=200, help_text="The id of the object in its structure, as signals spell it.")
    uuid = models.UUIDField(null=True, blank=True, help_text="The object's stable uuid, when it has one: what a reader keys on.")
    xid = Xid8Field(db_default=CurrentXid(), help_text="The writing transaction. Set by the database, never by the writer.")
    created_at = models.DateTimeField(db_default=Now())

    class Meta:
        indexes = (models.Index(fields=["feed", "organization", "xid", "id"], name="feed_entry_cursor_idx"),)

    def __str__(self) -> str:
        return f"{self.feed} {self.kind} {self.object}"


class FeedState(models.Model):
    """A feed's own uuid: part of its epoch, and kept with the data it describes."""

    feed = models.CharField(max_length=200, unique=True)
    token = models.UUIDField(default=uuid.uuid4)
    created_at = models.DateTimeField(db_default=Now())


class FeedCursor(models.Model):
    """How far a reader got in one organization's share of a producer's feed, and whether it stopped."""

    producer = models.CharField(max_length=200, help_text="The producer's name in the hub: `mikro`.")
    feed = models.CharField(max_length=200)
    organization = models.CharField(max_length=200)
    epoch = models.CharField(max_length=200, null=True, blank=True, help_text="The producer's epoch this cursor follows; null until the first page.")
    xid = models.BigIntegerField(default=0)
    entry = models.BigIntegerField(default=0, help_text="The id of the last entry applied, within `xid`.")
    halted = models.TextField(null=True, blank=True, help_text="Why reading stopped. Nothing is read until someone resumes it.")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = (models.UniqueConstraint(fields=["producer", "feed", "organization"], name="feed_cursor_unique"),)

    def __str__(self) -> str:
        return f"{self.producer} {self.feed} {self.organization} @ {self.xid}:{self.entry}"
