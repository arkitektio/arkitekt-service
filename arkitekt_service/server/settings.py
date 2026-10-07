"""The settings blocks every service of a hub spells the same way.

A service's ``configuration.py`` imports these and declares only what is its own::

    from arkitekt_service.server.settings import DjangoSettings, PostgresSettings, RedisSettings, ServiceSettings

    class Settings(ServiceSettings):
        django: DjangoSettings
        postgres: PostgresSettings
        redis: RedisSettings
        ...

A block a release spells differently is subclassed there, or written there in full.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource, SettingsConfigDict, YamlConfigSettingsSource

from arkitekt_service.contract.json_types import JSON

#: Names the config file a service reads, in its container.
CONFIG_FILE = "ARKITEKT_CONFIG_FILE"


def config_path() -> str:
    """The YAML file the settings are read from: ``ARKITEKT_CONFIG_FILE``, else ``config.yaml`` where the service runs."""
    return os.environ.get(CONFIG_FILE, "config.yaml")


def written() -> Mapping[str, JSON]:
    """What the config file says, as it is written: nothing, when there is none."""
    try:
        with open(config_path(), encoding="utf-8") as file:
            loaded: object = yaml.safe_load(file)
    except OSError:
        return {}
    return loaded if isinstance(loaded, dict) else {}  # pyright: ignore[reportUnknownVariableType]


class AdminSettings(BaseModel):
    """Django superuser created on first boot."""

    username: str = Field(description="Superuser login name.")
    password: str = Field(description="Superuser password. Secret — must be set.")
    email: str | None = Field(default=None, description="Superuser email address.")


class DjangoSettings(BaseModel):
    """Core Django framework settings."""

    secret_key: str = Field(description="Django SECRET_KEY for cryptographic signing. Secret — must be set.")
    debug: bool = Field(default=False, description="Enable Django debug mode (never in production).")
    log_level: str = Field(default="INFO", description="Root logger level (e.g. DEBUG, INFO, WARNING). The LOG_LEVEL env var overrides it.")
    enable_rich_logging: bool = Field(
        default=False, description="Render console logs with rich (colours, boxed tracebacks). A dev convenience; off by default, as plain one-line records suit container logs."
    )
    hosts: list[str] = Field(default_factory=lambda: ["*"], description="ALLOWED_HOSTS entries.")
    use_x_forwarded_host: bool = Field(default=True, description="Trust the X-Forwarded-Host header behind a reverse proxy.")
    admin: AdminSettings | None = Field(default=None, description="Superuser provisioned on first boot.")
    csrf_trusted_origins: list[str] = Field(default_factory=lambda: ["http://localhost", "https://localhost"], description="CSRF_TRUSTED_ORIGINS for unsafe (POST) requests.")
    force_script_name: str = Field(default="", description="URL path prefix (FORCE_SCRIPT_NAME) this service is served under.")


class PostgresSettings(BaseModel):
    """PostgreSQL database connection (Django ``DATABASES['default']``)."""

    model_config = ConfigDict(extra="allow")

    engine: str = Field(default="django.db.backends.postgresql", description="Django database backend (PostgreSQL).")
    db_name: str = Field(description="Database name.")
    username: str = Field(description="Database user.")
    password: str = Field(description="Database password. Secret — must be set.")
    host: str = Field(description="Database host.")
    port: int = Field(default=5432, description="Database port.")


class RedisSettings(BaseModel):
    """Redis connection (channel layer / cache).

    A service that sends on a channel layer subclasses this with a ``channel_prefix`` of its
    own: services on one redis that share a prefix deliver each other's events.
    """

    model_config = ConfigDict(extra="allow")

    host: str = Field(description="Redis host.")
    port: int = Field(default=6379, description="Redis port.")


class InstanceTrustSettings(BaseModel):
    """Where the hub's instance public keys come from: the coord's bundle, or inline."""

    jwks_uri: str | None = Field(default=None, description="The coord's hub-keys URL (the fakts `self.hub_keys_url`).")
    jwks: dict[str, Any] | None = Field(default=None, description="The bundle inline (a JWKS whose keys carry `service`), for a hub not enrolled yet.")


class InstanceSettings(BaseModel):
    """This instance's key — its only secret towards the hub's other services — and whom it trusts."""

    private_key: str = Field(description="Ed25519 private key (PKCS#8 PEM). Signs this service's requests to rekuest. Secret — must be set.")
    trust: InstanceTrustSettings = Field(default_factory=InstanceTrustSettings, description="The hub's trust bundle.")


class ServiceSettings(BaseSettings):
    """The base of a service's ``Settings``: read from the config file, with the environment over it.

    ``DJANGO__DEBUG=true`` in the environment overrides ``django.debug`` in the file.
    """

    model_config = SettingsConfigDict(env_nested_delimiter="__", extra="ignore")

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        return (init_settings, env_settings, YamlConfigSettingsSource(settings_cls, yaml_file=config_path()))
