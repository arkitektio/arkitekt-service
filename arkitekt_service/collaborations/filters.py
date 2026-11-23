"""Filtering any list by collaboration, and by the workflow that created it.

Mix :class:`SharingFilterMixin` into a model's strawberry-django filter::

    @strawberry_django.filter_type(models.Scene)
    class SceneFilter(SharingFilterMixin):
        ...

and the list takes ``inCollaboration: "<key>"`` (what a guest of that collaboration would see:
put in, or part of what was put in) and ``createdUnder: "<root task id>"`` (what the workflow
created). Both hold at any depth of a filter: nested under a relation, they narrow the related rows.
"""

import uuid
from typing import Any

import strawberry
import strawberry_django
from django.db.models import Model, Q, QuerySet

from arkitekt_service.collaborations.guests import held_q
from arkitekt_service.collaborations.models import Collaboration


def model_at(model: type[Model] | None, prefix: str) -> type[Model]:
    """The model a filter's ``prefix`` (``scene__``) leads to from ``model``."""
    assert model is not None, "A filter runs on a queryset of a model"
    for name in (part for part in prefix.split("__") if part):
        related = model._meta.get_field(name).related_model
        assert related is not None and not isinstance(related, str), (
            f"{model.__name__}.{name} leads to no model"
        )
        model = related
    return model


def _at(prefix: str, model: type[Model], condition: Q) -> Q:
    """``condition`` on ``model``'s rows, said of the rows ``prefix`` leads from."""
    if not prefix:
        return condition
    return Q(**{f"{prefix}pk__in": model._base_manager.filter(condition).values("pk")})


def collaboration_rows(key: object, organization: object) -> QuerySet[Any]:
    """This service's row of the collaboration ``key`` in ``organization`` (none for a key that is no key)."""
    try:
        parsed = uuid.UUID(str(key))
    except ValueError:
        return Collaboration.objects.none()
    return Collaboration.objects.filter(key=parsed, organization=organization)


def in_collaboration_q(model: type[Model], key: object, organization: object) -> Q:
    """``model``'s rows in the collaboration ``key``: put in, or part of what was put in."""
    return held_q(model, collaboration_rows(key, organization))


def created_under_q(
    model: type[Model], root_task_id: str, organization: object = None
) -> Q:
    """``model``'s rows the workflow ``root_task_id`` created: its structure says, else koherent.

    Nothing matches for a model that cannot say what created it.
    """
    from arkitekt_service.service.service import Service
    from arkitekt_service.service.structures import recorded_under

    structure = Service.hosting(model)
    condition = (
        structure.made_in(root_task_id, organization)
        if structure is not None
        else recorded_under(model, root_task_id, organization)
    )
    return condition if condition is not None else Q(pk__in=[])


def _organization(info: strawberry.Info) -> object:
    return info.context.request.organization


@strawberry.input(
    description="Filter by collaboration, and by the workflow that created it"
)
class SharingFilterMixin:
    """Drop-in mixin adding ``inCollaboration`` and ``createdUnder`` to a model's filter."""

    @strawberry_django.filter_field(
        description="Only what is in this collaboration (its key): put in, or part of what was"
    )
    def in_collaboration(
        self,
        value: strawberry.ID,
        prefix: str,
        queryset: QuerySet[Any],
        info: strawberry.Info,
    ) -> tuple[QuerySet[Any], Q]:
        model = model_at(queryset.model, prefix)
        return queryset, _at(
            prefix, model, in_collaboration_q(model, value, _organization(info))
        )

    @strawberry_django.filter_field(
        description="Only what the workflow (its root task id) created"
    )
    def created_under(
        self,
        value: strawberry.ID,
        prefix: str,
        queryset: QuerySet[Any],
        info: strawberry.Info,
    ) -> tuple[QuerySet[Any], Q]:
        model = model_at(queryset.model, prefix)
        return queryset, _at(
            prefix, model, created_under_q(model, str(value), _organization(info))
        )


__all__ = [
    "SharingFilterMixin",
    "collaboration_rows",
    "created_under_q",
    "in_collaboration_q",
    "model_at",
]
