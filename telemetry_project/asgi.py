"""As a service's ``asgi.py``: set up before Django's application is built, wrapped around the router."""

import os

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "telemetry_project.settings")

from arkitekt_service import telemetry  # noqa: E402

telemetry.setup()

from django.core.asgi import get_asgi_application  # noqa: E402

django_asgi_app = get_asgi_application()

from kante.router import router  # noqa: E402

from telemetry_project.schema import schema  # noqa: E402

application = telemetry.wrap(router(schema=schema, django_asgi_app=django_asgi_app))
