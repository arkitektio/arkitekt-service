"""Reading another service's feed: one signed request per page."""

from __future__ import annotations

import json
import threading
import uuid
from dataclasses import dataclass
from urllib.parse import urlparse

import httpx
from joserfc.jwk import OKPKey

from arkitekt_service import trust
from arkitekt_service.contract.json_types import JSON
from arkitekt_service.feed.api import Cursor

_TIMEOUT = 30.0


@dataclass(frozen=True)
class RemoteEntry:
    """An entry of another service's feed, with the body its producer rendered for it."""

    xid: int
    id: int
    kind: str
    object: str
    uuid: uuid.UUID | None
    organization: str
    body: JSON

    @property
    def cursor(self) -> Cursor:
        return Cursor(self.xid, self.id)


@dataclass(frozen=True)
class RemotePage:
    epoch: str
    entries: list[RemoteEntry]
    horizon: int
    current_xid: int


class FeedReader:
    """Reads ``feed`` from the service at ``url`` (its ``_feed`` endpoint), signing as ``issuer``."""

    _client_lock = threading.Lock()
    _client: httpx.Client | None = None

    def __init__(self, *, url: str, producer: str, feed: str, issuer: str, key: OKPKey | None = None) -> None:
        self.url = url
        #: What the reader signs with: this instance's key unless given (one process playing several instances: tests).
        self.key = key
        #: The producer's identifier: whom the request is addressed to (``aud``).
        self.producer = producer
        self.feed = feed
        self.issuer = issuer

    @classmethod
    def client(cls) -> httpx.Client:
        """One connection pool for every reader of the process."""
        with cls._client_lock:
            if cls._client is None:
                cls._client = httpx.Client(timeout=_TIMEOUT)
            return cls._client

    def page(self, organization: str, after: Cursor, limit: int = 1000) -> RemotePage:
        """The entries of ``organization`` after ``after``. Raises on any answer but a page."""
        body = json.dumps({"feed": self.feed, "organization": organization, "after": {"xid": str(after.xid), "id": after.id}, "limit": limit}).encode()
        authorization = trust.sign("POST", urlparse(self.url).path, body, issuer=self.issuer, audience=self.producer, key=self.key)
        response = self.client().post(self.url, content=body, headers={"Content-Type": "application/json", "Authorization": authorization})
        response.raise_for_status()
        answer = response.json()
        return RemotePage(
            epoch=str(answer["epoch"]),
            horizon=int(answer["horizon"]),
            current_xid=int(answer["current_xid"]),
            entries=[
                RemoteEntry(
                    xid=int(e["xid"]),
                    id=int(e["id"]),
                    kind=str(e["kind"]),
                    object=str(e["object"]),
                    uuid=None if e["uuid"] is None else uuid.UUID(e["uuid"]),
                    organization=str(e["organization"]),
                    body=e["body"],
                )
                for e in answer["entries"]
            ],
        )
