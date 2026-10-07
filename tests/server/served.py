"""A service whose settings are made of the shared blocks, as a real one's are."""

from __future__ import annotations

from arkitekt_service.contract import JSON, Contract, Description, Facts, Start, blocks
from arkitekt_service.server.settings import DjangoSettings, InstanceSettings, PostgresSettings, RedisSettings, ServiceSettings


class Redis(RedisSettings):
    channel_prefix: str = "served"


class Settings(ServiceSettings):
    django: DjangoSettings
    postgres: PostgresSettings
    redis: Redis
    instance: InstanceSettings | None = None


def render(facts: Facts) -> dict[str, JSON]:
    return blocks.server(facts)


contract = Contract(
    description=Description(name="served", identifier="live.arkitekt.served"),
    settings=Settings,
    render=render,
    serve=Start(("daphne", "served.asgi:application")),
    debug=Start(("python", "manage.py", "runserver")),
)
