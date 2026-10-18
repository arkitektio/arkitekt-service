from django.db import models

from arkitekt_service.service.described import Described


class Thing(Described):
    """A model the tests declare a model signal for."""

    name = models.CharField(max_length=100)
    organization = models.CharField(max_length=100, default="org")
    secret = models.BooleanField(default=False)

    class Meta:
        app_label = "test_app"


class Part(models.Model):
    """Written after its Thing, like mikro's axes after the dataset row."""

    thing = models.ForeignKey(Thing, on_delete=models.CASCADE, related_name="parts")

    class Meta:
        app_label = "test_app"


class Shelf(Described):
    """A model the tests declare a structure for."""

    name = models.CharField(max_length=100)
    organization = models.CharField(max_length=100, default="org")

    class Meta:
        app_label = "test_app"


class Book(Described):
    """Hosted and described, never signalled."""

    shelf = models.ForeignKey(Shelf, on_delete=models.CASCADE, related_name="books")

    class Meta:
        app_label = "test_app"


class Cabinet(Described):
    """Bound by the contract tests, so that no other service's describer writes its column."""

    name = models.CharField(max_length=100)
    organization = models.CharField(max_length=100, default="org")

    class Meta:
        app_label = "test_app"


class Crate(Described):
    """Keeps its descriptors: one read from its own field, one counted from rows written after it."""

    name = models.CharField(max_length=100)
    status = models.CharField(max_length=20, default="OPEN")
    organization = models.CharField(max_length=100, default="org")

    class Meta:
        app_label = "test_app"


class Bottle(models.Model):
    """Written after its crate, and counted by the crate's descriptors."""

    crate = models.ForeignKey(Crate, on_delete=models.CASCADE, related_name="bottles")

    class Meta:
        app_label = "test_app"


class Barrel(Described):
    """Bound by one test's service alone: described and signalled, then deleted before its update goes out."""

    name = models.CharField(max_length=100)
    organization = models.CharField(max_length=100, default="org")

    class Meta:
        app_label = "test_app"
