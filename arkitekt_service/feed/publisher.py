"""Serving a service's feeds to the services allowed to read them.

    publisher = FeedPublisher("live.arkitekt.mikro")
    publisher.feed("@mikro/metric", readers=["live.arkitekt.kraph"], render=render_metrics)

    urlpatterns = [..., *publisher.urls]          # POST <prefix>/_feed

A reader POSTs ``{"feed", "organization", "after": {"xid", "id"}, "limit"}``, signed with its
instance key (:mod:`arkitekt_service.trust`; the body is part of what is signed). The answer is a
page: ``{"feed", "epoch", "horizon", "current_xid", "entries": [...]}``, each entry with what
``render`` made of its object (null for one it has nothing for, a deleted one).

Who may read is the reader's *service*, not a user: a reader sees every organization's entries,
one organization per request. Keeping organizations apart is then the reader's discipline — it
reads the organization a run is for — and this endpoint's only promise is that nobody but the
named readers reads at all. Keep ``/<prefix>/_feed`` off the public edge like ``_rekuest``.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from django.conf import settings
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.urls import path
from django.views.decorators.csrf import csrf_exempt

from arkitekt_service import trust
from arkitekt_service.contract.json_types import JSON
from arkitekt_service.feed.api import Cursor, Entry, changes

logger = logging.getLogger(__name__)

#: What a feed makes of the objects of a page: their bodies, by object id.
Render = Callable[[list[Entry]], dict[str, JSON]]

MAX_LIMIT = 5000


@dataclass(frozen=True)
class Published:
    feed: str
    readers: frozenset[str]
    render: Render | None


class FeedPublisher:
    """The feeds one service serves, and who may read each."""

    def __init__(self, identifier: str) -> None:
        #: Whom readers address (``aud``): this instance's identifier in the trust bundle.
        self.identifier = identifier
        self._feeds: dict[str, Published] = {}

    def feed(self, feed: str, *, readers: Sequence[str], render: Render | None = None) -> None:
        """Serve ``feed`` to the services ``readers`` (their identifiers: ``live.arkitekt.kraph``)."""
        if feed in self._feeds:
            raise ValueError(f"The feed {feed!r} is published twice")
        self._feeds[feed] = Published(feed, frozenset(readers), render)

    @property
    def feeds(self) -> dict[str, Published]:
        return dict(self._feeds)

    def page(self, request: HttpRequest) -> HttpResponse:
        if request.method != "POST":
            return HttpResponse(status=405)
        try:
            verified = trust.verify("POST", request.path, request.body, request.headers.get("Authorization"), audience=self.identifier)
        except trust.TrustError as error:
            logger.info("Refused a feed read: %s", error)
            return JsonResponse({"error": "Invalid signature"}, status=401)
        try:
            asked = json.loads(request.body)
            feed = str(asked["feed"])
            organization = str(asked["organization"])
            after = Cursor(int(asked.get("after", {}).get("xid", 0)), int(asked.get("after", {}).get("id", 0)))
            limit = max(1, min(int(asked.get("limit", 1000)), MAX_LIMIT))
        except (ValueError, KeyError, TypeError, AttributeError):
            return JsonResponse({"error": "Expected feed, organization, after {xid, id} and limit"}, status=400)
        published = self._feeds.get(feed)
        if published is None:
            return JsonResponse({"error": f"No feed {feed!r}"}, status=404)
        if verified.issuer not in published.readers:
            logger.info("Refused %s a read of %s: not one of its readers", verified.issuer, feed)
            return JsonResponse({"error": "Not a reader of this feed"}, status=403)

        page = changes(feed, organization, after, limit)
        bodies = published.render(page.entries) if published.render and page.entries else {}
        return JsonResponse(
            {
                "feed": feed,
                "epoch": page.epoch,
                # As strings: a JSON number is not safe past 2**53, an xid8 may be.
                "horizon": str(page.horizon),
                "current_xid": str(page.current_xid),
                "entries": [
                    {
                        "xid": str(e.xid),
                        "id": e.id,
                        "kind": e.kind,
                        "object": e.object,
                        "uuid": None if e.uuid is None else str(e.uuid),
                        "organization": e.organization,
                        "body": bodies.get(e.object),
                    }
                    for e in page.entries
                ],
            }
        )

    @property
    def urls(self) -> list:
        """``POST <prefix>/_feed``, bound to this publisher. Mount it in ``urls.py``."""

        @csrf_exempt
        def page_view(request: HttpRequest) -> HttpResponse:
            return self.page(request)

        prefix = (getattr(settings, "MY_SCRIPT_NAME", "") or "").strip("/")
        return [path(f"{prefix}/_feed" if prefix else "_feed", page_view, name="arkitekt_feed")]
