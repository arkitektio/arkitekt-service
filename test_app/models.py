from django.db import models


class Thing(models.Model):
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


class Shelf(models.Model):
    """A model the tests declare a structure for."""

    name = models.CharField(max_length=100)
    organization = models.CharField(max_length=100, default="org")

    class Meta:
        app_label = "test_app"


class Book(models.Model):
    """Hosted and described, never signalled."""

    shelf = models.ForeignKey(Shelf, on_delete=models.CASCADE, related_name="books")

    class Meta:
        app_label = "test_app"
