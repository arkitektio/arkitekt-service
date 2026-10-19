"""The change feed against a real Postgres: written with the change, read in commit-safe order."""

import threading
import uuid

import httpx
import pytest
from django.conf import settings
from django.db import connection, transaction

from arkitekt_service import trust
from arkitekt_service.feed.api import Change, Cursor, NotInTransaction, changes, record_change, record_changes
from arkitekt_service.feed.consumer import Ended, Poison, apply_pages, resume
from arkitekt_service.feed.models import FeedCursor, FeedEntry, FeedState
from arkitekt_service.feed.reader import FeedReader

pytestmark = pytest.mark.django_db(transaction=True)

METRIC = "@mikro/metric"


def created(obj: str, organization: str = "lab") -> Change:
    return Change(METRIC, "CREATED", obj, organization, uuid=uuid.uuid4())


def in_thread(work):
    """Run ``work`` on a thread of its own — its own connection, its own transaction."""
    errors: list[BaseException] = []

    def run():
        try:
            work()
        except BaseException as error:  # noqa: BLE001
            errors.append(error)
        finally:
            connection.close()

    thread = threading.Thread(target=run)
    thread.start()
    return thread, errors


class TestWriting:
    def test_a_change_is_recorded_only_inside_the_transaction_that_makes_it(self):
        with pytest.raises(NotInTransaction):
            record_change(created("1"))

    def test_a_rolled_back_write_leaves_no_entry(self):
        with pytest.raises(RuntimeError), transaction.atomic():
            record_change(created("1"))
            raise RuntimeError("the write failed")
        assert FeedEntry.objects.count() == 0

    def test_the_database_stamps_the_writing_transaction(self):
        with transaction.atomic():
            record_changes([created("1"), created("2")])
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_current_xact_id()::text")
                (mine,) = cursor.fetchone()
        assert set(FeedEntry.objects.values_list("xid", flat=True)) == {int(mine)}


class TestOrder:
    def test_an_open_transaction_holds_back_what_committed_after_it(self):
        """T takes its xid first, R second; R commits first. Nothing may be handed out past T
        while T is open, or a reader that moved its cursor past R would never see T."""
        t_wrote, t_may_commit = threading.Event(), threading.Event()

        def t():
            with transaction.atomic():
                record_change(created("t"))
                t_wrote.set()
                t_may_commit.wait(10)

        thread, errors = in_thread(t)
        assert t_wrote.wait(10)
        with transaction.atomic():
            record_change(created("r"))

        held = changes(METRIC, "lab")
        assert held.entries == [] and held.horizon <= max(FeedEntry.objects.values_list("xid", flat=True))

        t_may_commit.set()
        thread.join(10)
        assert not errors
        assert [e.object for e in changes(METRIC, "lab").entries] == ["t", "r"]

    def test_a_cursor_never_skips_a_late_commit(self):
        """Read, move the cursor, read again: every entry once, whatever the commit order."""
        t_wrote, t_may_commit = threading.Event(), threading.Event()

        def t():
            with transaction.atomic():
                record_change(created("late"))
                t_wrote.set()
                t_may_commit.wait(10)

        with transaction.atomic():
            record_change(created("early"))
        thread, errors = in_thread(t)
        assert t_wrote.wait(10)
        with transaction.atomic():
            record_change(created("after"))

        seen: list[str] = []
        page = changes(METRIC, "lab")
        seen += [e.object for e in page.entries]
        cursor = page.entries[-1].cursor
        t_may_commit.set()
        thread.join(10)
        assert not errors
        seen += [e.object for e in changes(METRIC, "lab", cursor).entries]
        assert seen == ["early", "late", "after"]

    def test_a_page_is_one_organization(self):
        with transaction.atomic():
            record_changes([created("a1", "a"), created("b1", "b"), created("a2", "a")])
        assert [e.object for e in changes(METRIC, "a").entries] == ["a1", "a2"]
        assert [e.object for e in changes(METRIC, "b").entries] == ["b1"]

    def test_pages_follow_the_cursor(self):
        with transaction.atomic():
            record_changes([created(str(i)) for i in range(5)])
        first = changes(METRIC, "lab", limit=2)
        second = changes(METRIC, "lab", first.entries[-1].cursor, limit=10)
        assert [e.object for e in first.entries + second.entries] == ["0", "1", "2", "3", "4"]


class TestPublishing:
    def post(self, live_server, body: bytes, key=None, issuer="live.arkitekt.kraph") -> httpx.Response:
        url = f"{live_server.url}/_feed"
        headers = {"Content-Type": "application/json"}
        if key is not None:
            headers["Authorization"] = trust.sign("POST", "/_feed", body, issuer=issuer, audience="live.arkitekt.mikro", key=key)
        return httpx.post(url, content=body, headers=headers)

    def test_only_a_named_reader_reads(self, live_server):
        body = b'{"feed": "@mikro/metric", "organization": "lab", "after": {"xid": "0", "id": 0}, "limit": 10}'
        assert self.post(live_server, body).status_code == 401
        assert self.post(live_server, body, key=settings.STRANGER_KEY, issuer="live.arkitekt.stranger").status_code == 403
        assert self.post(live_server, body, key=settings.READER_KEY).status_code == 200

    def test_a_page_carries_what_the_producer_rendered(self, reader):
        with transaction.atomic():
            record_change(created("8812"))
        page = reader.page("lab", Cursor())
        (entry,) = page.entries
        assert (entry.object, entry.kind, entry.body) == ("8812", "CREATED", {"seen": "8812"})
        assert page.epoch.endswith(str(FeedState.objects.get(feed=METRIC).token))


def collect(into: list):
    def apply(entries):
        into.extend(e.object for e in entries)

    return apply


class TestConsuming:
    def test_everything_is_applied_once_and_the_cursor_follows(self, reader):
        with transaction.atomic():
            record_changes([created(str(i)) for i in range(25)])
        applied: list[str] = []
        outcome = apply_pages(reader, "lab", collect(applied), limit=10)
        assert outcome.ended == Ended.CAUGHT_UP and outcome.applied == 25 and outcome.pages == 3
        assert applied == [str(i) for i in range(25)]

        again: list[str] = []
        assert apply_pages(reader, "lab", collect(again)).applied == 0 and again == []

    def test_a_second_run_gives_way_to_the_one_reading(self, reader):
        with transaction.atomic():
            record_change(created("1"))
        holding, release = threading.Event(), threading.Event()

        def hold():
            with transaction.atomic(), connection.cursor() as cursor:
                cursor.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", ["arkitekt-feed:live.arkitekt.mikro:@mikro/metric:lab"])
                holding.set()
                release.wait(10)

        thread, errors = in_thread(hold)
        assert holding.wait(10)
        applied: list[str] = []
        assert apply_pages(reader, "lab", collect(applied)).ended == Ended.BUSY and applied == []
        release.set()
        thread.join(10)
        assert not errors and apply_pages(reader, "lab", collect(applied)).applied == 1

    def test_a_poison_entry_halts_and_keeps_the_cursor(self, reader):
        with transaction.atomic():
            record_changes([created("good"), created("bad")])

        def apply(entries):
            FeedEntry.objects.filter(object="good").update(object="was-applied")  # rolled back with the page
            raise Poison("no such unit")

        outcome = apply_pages(reader, "lab", apply)
        assert outcome.ended == Ended.HALTED and "no such unit" in outcome.halted
        row = FeedCursor.objects.get(organization="lab")
        assert (row.xid, row.entry) == (0, 0) and FeedEntry.objects.filter(object="good").exists()

        applied: list[str] = []
        assert apply_pages(reader, "lab", collect(applied)).ended == Ended.HALTED and applied == []
        resume("live.arkitekt.mikro", METRIC, "lab")
        assert apply_pages(reader, "lab", collect(applied)).ended == Ended.CAUGHT_UP and applied == ["good", "bad"]

    def test_any_other_error_fails_the_run_and_the_next_run_retries(self, reader):
        with transaction.atomic():
            record_change(created("1"))

        def flaky(entries):
            raise ConnectionError("the database went away")

        with pytest.raises(ConnectionError):
            apply_pages(reader, "lab", flaky)
        assert FeedCursor.objects.get(organization="lab").halted is None
        applied: list[str] = []
        assert apply_pages(reader, "lab", collect(applied)).applied == 1

    def test_a_restored_producer_halts_the_reader(self, reader):
        with transaction.atomic():
            record_change(created("1"))
        apply_pages(reader, "lab", collect([]))
        FeedState.objects.filter(feed=METRIC).update(token=uuid.uuid4())  # another history

        with transaction.atomic():
            record_change(created("2"))
        outcome = apply_pages(reader, "lab", collect([]))
        assert outcome.ended == Ended.HALTED and "epoch" in outcome.halted

    def test_a_counter_that_went_back_halts_the_reader(self, reader):
        apply_pages(reader, "lab", collect([]))
        FeedCursor.objects.filter(organization="lab").update(xid=2**62)  # a cursor from a cluster that had gone further
        outcome = apply_pages(reader, "lab", collect([]))
        assert outcome.ended == Ended.HALTED and "went back" in outcome.halted

    def test_organizations_have_cursors_of_their_own(self, reader):
        with transaction.atomic():
            record_changes([created("a1", "a"), created("b1", "b")])
        a: list[str] = []
        b: list[str] = []
        apply_pages(reader, "a", collect(a))
        apply_pages(reader, "b", collect(b))
        assert (a, b) == (["a1"], ["b1"])
        assert FeedCursor.objects.count() == 2
