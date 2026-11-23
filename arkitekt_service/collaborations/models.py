"""A collaboration, and what was put in it."""

from __future__ import annotations

import uuid

from django.conf import settings
from django.contrib.contenttypes.models import ContentType
from django.db import models


class Collaboration(models.Model):
    """What a guest granted it sees of an organization: the objects put in it, and their parts.

    A collaboration is lok's, one ``key`` across the hub: a guest's token names it by that key,
    and every service of the organization holds what it put in it under a row with the same key,
    made the first time something is put in (:func:`ensure`). The name is a label, given by
    whoever put the first thing in; lok's is the one people read.
    """

    key = models.UUIDField(
        unique=True,
        default=uuid.uuid4,
        help_text="The collaboration's key across the hub: lok's, and what a guest's token names",
    )
    organization = models.ForeignKey(
        "authentikate.Organization",
        on_delete=models.CASCADE,
        related_name="collaborations",
    )
    name = models.CharField(max_length=200, blank=True, default="")
    description = models.TextField(null=True, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self) -> str:
        return str(self.name or self.key)


class CollaborationOfAnotherOrganization(ValueError):
    """The key names a collaboration another organization put objects in here."""


def ensure(
    key: uuid.UUID | str,
    organization: object,
    *,
    name: str | None = None,
    by: object = None,
) -> Collaboration:
    """This service's row of the collaboration ``key``, made on first use for ``organization``.

    A key another organization uses here is refused: a collaboration is one organization's.
    """
    found, created = Collaboration.objects.get_or_create(
        key=key,
        defaults={"organization": organization, "name": name or "", "created_by": by},
    )
    if not created and found.organization_id != getattr(organization, "pk", None):  # type: ignore[attr-defined]
        raise CollaborationOfAnotherOrganization(
            f"Collaboration {key} belongs to another organization"
        )
    if not created and name and not found.name:
        found.name = name
        found.save(update_fields=["name"])
    return found


class CollaborationItem(models.Model):
    """One object in a collaboration: put there, or along with one that needs it (``because_of``)."""

    collaboration = models.ForeignKey(
        Collaboration, on_delete=models.CASCADE, related_name="items"
    )
    content_type = models.ForeignKey(
        ContentType, on_delete=models.CASCADE, related_name="+"
    )
    object_id = models.CharField(max_length=200)
    because_of = models.ForeignKey(
        "self",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="brought",
        help_text="The item this one was put in along with, as what it needs to be seen; null when put in by hand",
    )
    added_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    added_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = (
            models.UniqueConstraint(
                fields=["collaboration", "content_type", "object_id"],
                name="an_object_is_in_a_collaboration_once",
            ),
        )
        indexes = (
            models.Index(
                fields=["content_type", "object_id"], name="collab_items_of_an_object"
            ),
        )

    def __str__(self) -> str:
        return f"{self.content_type.model}:{self.object_id} in {self.collaboration_id}"
