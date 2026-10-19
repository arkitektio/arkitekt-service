"""A real Postgres for the feed (dokker, a port docker picks), and readers signing as their instances."""

import os
import time

import psycopg
import pytest
from django.conf import settings as django_settings
from dokker import PortNotFoundError, testing

from arkitekt_service.feed.reader import FeedReader


@pytest.fixture(scope="session")
def backend_stack():
    with testing(os.path.join(os.path.dirname(__file__), "docker-compose.yaml")) as deployment:
        deployment.up()
        port = None
        deadline = time.monotonic() + 60
        while True:
            try:
                if port is None:
                    port = deployment.get_port("db", 5432)
                with psycopg.connect(dbname="testdb", user="test", password="test", host="localhost", port=port, connect_timeout=1) as connection:
                    connection.execute("SELECT 1")
                break
            except (psycopg.OperationalError, PortNotFoundError):
                if time.monotonic() >= deadline:
                    raise
                time.sleep(0.2)
        yield port


@pytest.fixture(scope="session")
def django_db_modify_db_settings(backend_stack):
    django_settings.DATABASES["default"]["PORT"] = str(backend_stack)


@pytest.fixture
def reader(live_server):
    """kraph reading mikro's metric feed, over HTTP, signed with kraph's key."""
    return FeedReader(url=f"{live_server.url}/_feed", producer="live.arkitekt.mikro", feed="@mikro/metric", issuer="live.arkitekt.kraph", key=django_settings.READER_KEY)
