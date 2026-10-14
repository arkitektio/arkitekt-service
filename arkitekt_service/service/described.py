"""A row that keeps its descriptors: what a structure says about its objects, stored on them.

Every descriptor a structure declares is stored, never only computed when asked. A port's
``requires`` is then a query on one column (:mod:`arkitekt_service.service.requires`), and a
signal, the service's ``descriptors`` field and a picker's filter all read the same value.

    class Transaction(Described):
        ...

        class Meta:
            indexes = [GinIndex(fields=["stored_descriptors"], name="bank_tx_desc_gin", opclasses=["jsonb_path_ops"])]

A hosted model with descriptors has to be one (``Service.structure`` refuses it otherwise).
Binding it gives it a ``pre_save`` hook that recomputes the column on every save, so a
descriptor read from the row's own fields (a status, a kind) never goes stale. Each concrete
model declares its own GIN index, with a name of at most 30 characters.

What the hook cannot see is written by someone else, who calls ``service.restore``:

- rows written after this one that a descriptor counts (axes, annotations, attached objects),
- many-to-many changes,
- bulk writes (``bulk_create``, ``bulk_update``, ``update()``), which send no ``pre_save``.
  For the first two, ``service.stamp(rows)`` describes the rows before they are written.

A describer that raises keeps the value the row had (``{}`` for a new row) and logs one warning:
a save never fails because it could not be described, and whoever writes what the describer was
missing restores it. The setup job ``restore_descriptors`` repairs every row that misses a
declared key.
"""

from __future__ import annotations

from typing import Any

from django.db import models


class Described(models.Model):
    """An abstract model whose rows keep their descriptors in ``stored_descriptors``."""

    stored_descriptors = models.JSONField(
        default=dict,
        blank=True,
        # Held by the database too: a rolled-back build inserts rows without naming it.
        db_default=models.Value({}, output_field=models.JSONField()),
        help_text="The descriptors of this object, as its structure declares them: stored on every save so that a `requires` filter reads them in SQL",
    )

    class Meta:
        abstract = True

    def save(self, *args: Any, **kwargs: Any) -> None:
        # A save of some fields still writes what they describe: the hook recomputes the
        # column, and without it in ``update_fields`` the new value would never be written.
        update_fields = kwargs.get("update_fields")
        if update_fields:
            kwargs["update_fields"] = {*update_fields, "stored_descriptors"}
        super().save(*args, **kwargs)
