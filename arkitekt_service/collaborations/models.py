"""A collaboration, and what was put in it."""

from __future__ import annotations

from django.conf import settings
from django.contrib.contenttypes.models import ContentType
from django.db import models


class Collaboration(models.Model):
    """What a guest granted it sees of an organization: the objects put in it, and their parts."""

    organization = models.ForeignKey("authentikate.Organization", on_delete=models.CASCADE, related_name="collaborations")
    name = models.CharField(max_length=200)
    description = models.TextField(null=True, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self) -> str:
        return self.name


class CollaborationItem(models.Model):
    """One object in a collaboration: put there, or along with one that needs it (``because_of``)."""

    collaboration = models.ForeignKey(Collaboration, on_delete=models.CASCADE, related_name="items")
    content_type = models.ForeignKey(ContentType, on_delete=models.CASCADE, related_name="+")
    object_id = models.CharField(max_length=200)
    because_of = models.ForeignKey(
        "self",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="brought",
        help_text="The item this one was put in along with, as what it needs to be seen; null when put in by hand",
    )
    added_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    added_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = (models.UniqueConstraint(fields=["collaboration", "content_type", "object_id"], name="an_object_is_in_a_collaboration_once"),)
        indexes = (models.Index(fields=["content_type", "object_id"], name="collab_items_of_an_object"),)

    def __str__(self) -> str:
        return f"{self.content_type.model}:{self.object_id} in {self.collaboration_id}"
