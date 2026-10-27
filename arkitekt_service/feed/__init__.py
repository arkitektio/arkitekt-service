"""A change feed: what a service changed, durably, for another service to read in order.

Signals (:mod:`arkitekt_service.service`) are best effort: a queue in the process, dropped when
full, lost when the process dies. A feed is the durable half. The producer records a change in
the **same transaction** as the change itself, so a rolled-back write leaves no entry and a
committed one always has its entry::

    from arkitekt_service.feed.api import Change, record_change

    with transaction.atomic():
        metric = Metric.objects.create(...)
        record_change(Change("@mikro/metric", "CREATED", str(metric.pk), organization.identifier, uuid=metric.uuid))

and serves it to the services allowed to read it (``*publisher.urls``)::

    publisher = FeedPublisher("mikro")
    publisher.feed("@mikro/metric", readers=["kraph"], render=render_metrics)

A reader pulls pages after its cursor and applies each page in one transaction with the cursor
(:func:`arkitekt_service.feed.consumer.apply_pages`): nothing is lost, nothing applied twice.

**Order.** Entries are read in ``(xid, id)`` order, where ``xid`` is the writing transaction's id
(``pg_current_xact_id()``, assigned by the database). A page only holds entries of transactions
older than every transaction still open (``xid < pg_snapshot_xmin(pg_current_snapshot())``), so
no entry can later commit *below* a cursor already handed out. That needs no relay, no sequence
and no lock on the write path. The price: an open transaction holds the feed back for as long as
it is open — and the snapshot is the whole cluster's, so a long transaction in *any* database of
the cluster does. ``horizon`` and ``current_xid`` on every page say how far behind the feed is.

**Restores.** A page carries the feed's ``epoch``: the cluster's system identifier, the database's
oid and a uuid kept with the feed. A restore into another cluster or database changes it, and
the reader halts rather than read a history that is no longer the one it followed. The reader
also halts when the producer's ``current_xid`` is below its cursor — a counter that went back.

The modules: :mod:`~arkitekt_service.feed.api` (write and read, in the producer),
:mod:`~arkitekt_service.feed.publisher` (serve), :mod:`~arkitekt_service.feed.reader` and
:mod:`~arkitekt_service.feed.consumer` (read and apply, in the reader). The package itself imports
nothing: it is a Django app (``"arkitekt_service.feed"`` in ``INSTALLED_APPS``), loaded before its
models may be.
"""
