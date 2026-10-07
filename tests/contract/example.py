"""A small service, declared the way a real one is: settings, and a config written from facts."""

from __future__ import annotations

import os

from pydantic import AliasChoices, BaseModel, ConfigDict, Field
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource, SettingsConfigDict, YamlConfigSettingsSource

from arkitekt_service.contract import JSON, Contract, Description, Facts, Needs, Offers, Refused, Start


class Django(BaseModel):
    secret_key: str
    debug: bool = False
    force_script_name: str = ""


class Postgres(BaseModel):
    model_config = ConfigDict(extra="allow")

    host: str
    db_name: str
    password: str


class Hook(BaseModel):
    rekuest_url: str = Field(validation_alias=AliasChoices("rekuest_url", "agent_url"))
    max_skew: int = 30


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_nested_delimiter="__", extra="ignore")

    django: Django
    postgres: Postgres
    rekuest_hook: Hook | None = None

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        return (init_settings, env_settings, YamlConfigSettingsSource(settings_cls, yaml_file=os.environ.get("ARKITEKT_CONFIG_FILE", "config.yaml")))


def render(facts: Facts) -> dict[str, JSON]:
    database = facts.databases.get("main")
    if database is None:
        raise Refused("it needs a database, and this hub gives it none")
    document: dict[str, JSON] = {
        "django": {"secret_key": facts.me.secret_key, "debug": facts.me.debug, "force_script_name": facts.me.path},
        "postgres": {"host": database.host, "db_name": database.name, "password": database.password},
    }
    agents = facts.offering("agent")
    if agents:
        document["rekuest_hook"] = {"rekuest_url": next(iter(agents.values())).offers["agent"]}
    return document


contract = Contract(
    description=Description(
        name="example",
        identifier="live.arkitekt.example",
        needs=Needs(storage=["media"], instance_key=True, peers=["rekuest"]),
        offers=Offers(endpoints={"rekuest_hook": "_rekuest/hook"}),
        requires={"rekuest": ">=6"},
        upgrade_from="1.0.0",
    ),
    settings=Settings,
    render=render,
    serve=Start(("daphne", "-b", "0.0.0.0", "-p", "80", "example.asgi:application")),
    debug=Start(("python", "manage.py", "runserver", "0.0.0.0:80"), {"EXAMPLE_DEBUG": "1"}),
)
