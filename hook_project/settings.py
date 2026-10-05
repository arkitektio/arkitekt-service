"""A minimal Django project for rekuest-hook's tests: no models, no service, one HookAgent mounted.

Two real Ed25519 keys: the instance's own (``INSTANCE``) and one standing in for rekuest's, which
tests use to sign requests to the agent. Both are in the inline trust bundle, as the coord
would publish them.
"""

from joserfc.jwk import OKPKey
from arkitekt_service.trust import public_jwk

SECRET_KEY = "rekuest-hook-tests"
DEBUG = True
ALLOWED_HOSTS = ["*"]
INSTALLED_APPS = ["django.contrib.contenttypes"]
DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}}
ROOT_URLCONF = "hook_project.urls"
USE_TZ = True
MY_SCRIPT_NAME = ""

INSTANCE_KEY = OKPKey.generate_key("Ed25519")
REKUEST_KEY = OKPKey.generate_key("Ed25519")
TRUST_JWKS = {
    "keys": [
        {**public_jwk(INSTANCE_KEY), "service": "live.arkitekt.worker"},
        {**public_jwk(REKUEST_KEY), "service": "live.arkitekt.rekuest"},
    ]
}
INSTANCE = {"PRIVATE_KEY": INSTANCE_KEY.as_pem(private=True).decode(), "TRUST_JWKS": TRUST_JWKS}

# Tests point REKUEST_URL at a local HTTP server standing in for rekuest's intake.
REKUEST_HOOK = {"REKUEST_URL": "http://127.0.0.1:9", "MAX_SKEW": 30}
