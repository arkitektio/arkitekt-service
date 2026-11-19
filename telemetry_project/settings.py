"""The telemetry tests' service: a GraphQL schema behind kante's router, as a real service is served.

Run as a process of its own (``tests/telemetry/conftest.py`` starts daphne on it): what
OpenTelemetry sets up is per process and cannot be undone, so "off" and "on" are two servers.
"""

import os

SECRET_KEY = "arkitekt-telemetry-tests"
DEBUG = True
ALLOWED_HOSTS = ["*"]
INSTALLED_APPS = ["django.contrib.contenttypes", "django.contrib.auth", "arkitekt_service.server"]
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": "testdb",
        "USER": "test",
        "PASSWORD": "test",
        "HOST": "localhost",
        "PORT": os.environ.get("TELEMETRY_TEST_DB_PORT", "5432"),
    }
}
ROOT_URLCONF = "telemetry_project.urls"
USE_TZ = True
MY_SCRIPT_NAME = ""
