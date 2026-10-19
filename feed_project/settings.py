"""The change feed's tests: a real Postgres (xid8, snapshots), brought up by dokker per run.

One process plays two instances: the producer (``INSTANCE``, live.arkitekt.mikro) and its reader
(``READER_KEY``, live.arkitekt.kraph), plus a stranger the bundle vouches for but no feed names.
"""

import os

from joserfc.jwk import OKPKey

from arkitekt_service.trust import public_jwk

SECRET_KEY = "arkitekt-feed-tests"
DEBUG = True
ALLOWED_HOSTS = ["*"]
INSTALLED_APPS = ["django.contrib.contenttypes", "django.contrib.auth", "arkitekt_service.feed"]
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": "testdb",
        "USER": "test",
        "PASSWORD": "test",
        "HOST": "localhost",
        # Replaced by the port docker picked (tests/feed/conftest.py); this is for a stack started by hand.
        "PORT": os.environ.get("FEED_TEST_DB_PORT", "5432"),
    }
}
ROOT_URLCONF = "feed_project.urls"
USE_TZ = True
MY_SCRIPT_NAME = ""
# The live server (the publisher served over HTTP) wraps the app in a static-files handler.
STATIC_URL = "/static/"

PRODUCER_KEY = OKPKey.generate_key("Ed25519")
READER_KEY = OKPKey.generate_key("Ed25519")
STRANGER_KEY = OKPKey.generate_key("Ed25519")
TRUST_JWKS = {
    "keys": [
        {**public_jwk(PRODUCER_KEY), "service": "live.arkitekt.mikro"},
        {**public_jwk(READER_KEY), "service": "live.arkitekt.kraph"},
        {**public_jwk(STRANGER_KEY), "service": "live.arkitekt.stranger"},
    ]
}
INSTANCE = {"PRIVATE_KEY": PRODUCER_KEY.as_pem(private=True).decode(), "TRUST_JWKS": TRUST_JWKS}
