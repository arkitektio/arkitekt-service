"""One query that reads the database, one that asks another service (itself) over HTTP."""

import httpx
import strawberry
from asgiref.sync import sync_to_async
from django.db import connection

from arkitekt_service import telemetry
from arkitekt_service.graphql import TelemetryExtension


def _one() -> int:
    with connection.cursor() as cursor:
        cursor.execute("SELECT 1")
        return int(cursor.fetchone()[0])


@strawberry.type
class Query:
    @strawberry.field
    async def one(self) -> int:
        return await sync_to_async(_one)()

    @strawberry.field
    def fails(self) -> int:
        raise ValueError("as asked")

    @strawberry.field
    async def asked(self, url: str) -> str | None:
        """The ``traceparent`` another service sees when this one asks it something, the way the package's own callers do."""
        async with httpx.AsyncClient(timeout=5) as client:
            return (await client.get(url, headers=telemetry.headers())).json()["traceparent"]


schema = strawberry.Schema(query=Query, extensions=[TelemetryExtension])
