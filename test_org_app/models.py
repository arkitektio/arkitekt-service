"""Rows organizations own, linked the ways a service links them: the prune command's tests."""

from django.db import models


class Space(models.Model):
    organization = models.ForeignKey("authentikate.Organization", on_delete=models.CASCADE, related_name="+")


class Item(models.Model):
    """Protects nothing, is protected by nothing but its space: a dataset in its system."""

    organization = models.ForeignKey("authentikate.Organization", on_delete=models.CASCADE, related_name="+")
    space = models.ForeignKey(Space, on_delete=models.PROTECT, related_name="items")


class Child(models.Model):
    """No organization of its own: goes by cascade."""

    item = models.ForeignKey(Item, on_delete=models.CASCADE, related_name="children")


class Note(models.Model):
    """A correction protects what it supersedes, as metrics do."""

    organization = models.ForeignKey("authentikate.Organization", on_delete=models.CASCADE, related_name="+")
    supersedes = models.ForeignKey("self", on_delete=models.PROTECT, null=True, blank=True, related_name="superseded_by")


class Link(models.Model):
    """Another organization's row on an item: a guest's lens over a shared container."""

    organization = models.ForeignKey("authentikate.Organization", on_delete=models.CASCADE, related_name="+")
    item = models.ForeignKey(Item, on_delete=models.CASCADE, related_name="links")


class Board(models.Model):
    """What a pin belongs to: a scene its layers do."""

    organization = models.ForeignKey("authentikate.Organization", on_delete=models.CASCADE, related_name="+")


class Pin(models.Model):
    """No organization of its own, and two parents it dies with: a layer on its scene, drawing through a lens."""

    board = models.ForeignKey(Board, on_delete=models.CASCADE, related_name="pins")
    item = models.ForeignKey(Item, on_delete=models.CASCADE, related_name="pins")
