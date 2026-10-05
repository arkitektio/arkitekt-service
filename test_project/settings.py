"""A minimal Django project for rekuest-service's tests: no models, one Service mounted.

Two real Ed25519 keys: the service's own (``INSTANCE``) and one standing in for rekuest's, which
tests use to sign requests to the service. Both are in the inline trust bundle, as the coord
would publish them.
"""

from joserfc.jwk import OKPKey

from arkitekt_service.trust import public_jwk

SECRET_KEY = "rekuest-service-tests"
DEBUG = True
ALLOWED_HOSTS = ["*"]
INSTALLED_APPS = ["django.contrib.contenttypes", "test_app"]
DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}}
ROOT_URLCONF = "test_project.urls"
USE_TZ = True
MY_SCRIPT_NAME = ""

SERVICE_KEY = OKPKey.generate_key("Ed25519")
REKUEST_KEY = OKPKey.generate_key("Ed25519")
# The model-signal tests' own services, each with its own key (a key belongs to one service).
THINGS_KEY = OKPKey.generate_key("Ed25519")
BROKEN_KEY = OKPKey.generate_key("Ed25519")
TRUST_JWKS = {
    "keys": [
        {**public_jwk(SERVICE_KEY), "service": "live.arkitekt.testsvc"},
        {**public_jwk(REKUEST_KEY), "service": "live.arkitekt.rekuest"},
        {**public_jwk(THINGS_KEY), "service": "live.arkitekt.things"},
        {**public_jwk(BROKEN_KEY), "service": "live.arkitekt.broken"},
    ]
}
INSTANCE = {"PRIVATE_KEY": SERVICE_KEY.as_pem(private=True).decode(), "TRUST_JWKS": TRUST_JWKS}

# Tests point REKUEST_URL at a local HTTP server standing in for rekuest's intake.
REKUEST_SERVICE = {"REKUEST_URL": "http://127.0.0.1:9", "MAX_SKEW": 30}
