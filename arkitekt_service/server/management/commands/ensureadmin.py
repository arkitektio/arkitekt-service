"""Ensure the operator account the configuration names exists.

Reads the optional ``django.admin`` block of the service's configuration and creates that
superuser if it does not exist. Run again, it changes nothing: an account that is there is left
as it is, password included. Does nothing, and succeeds, when no ``django.admin`` is configured.
"""

from __future__ import annotations

from typing import Any

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand

from arkitekt_service.contract import contract


class Command(BaseCommand):
    help = "Create the configured superuser (django.admin) if it does not already exist."
    requires_migrations_checks = True

    def handle(self, *args: Any, **options: Any) -> None:
        django = getattr(contract.load().settings(), "django", None)
        admin = getattr(django, "admin", None)
        if admin is None:
            self.stdout.write("No django.admin configured: no account to create.")
            return

        users = get_user_model()
        if users.objects.filter(username=admin.username).exists():
            self.stdout.write(f"Superuser '{admin.username}' already exists: nothing to do.")
            return

        users.objects.create_superuser(username=admin.username, email=admin.email or "", password=admin.password)  # pyright: ignore[reportAttributeAccessIssue]
        self.stdout.write(self.style.SUCCESS(f"Created superuser '{admin.username}'."))
