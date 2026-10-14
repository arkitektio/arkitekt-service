"""A port's ``requires``, asked of stored descriptors in SQL: which rows satisfy these constraints.

The other direction of a describer. A describer says what one object is; this says which
objects are something, so a picker for a port pages through what the port accepts without a
describer run per row. It reads only ``stored_descriptors`` (:class:`~arkitekt_service.service.described.Described`):
equality is JSONB containment, which a ``jsonb_path_ops`` GIN index serves.

A constraint is ``{key, operator, value}``, the shape a rekuest port states it in. ``MATCHES``
means what ``EQUALS`` means. Every constraint must hold. A key the structure does not declare
is refused, never ignored: a filter that silently dropped a constraint would offer objects the
port then refuses.

The GraphQL input stays the service's (this package does not depend on strawberry); anything
with ``key``, ``operator`` (a name, or an enum whose value is one) and ``value`` is a constraint.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from enum import Enum
from typing import Protocol

from django.db.models import Q

OPERATORS = ("EQUALS", "MATCHES", "NOT_EQUALS", "GTE", "LTE", "EXISTS", "CONTAINS", "IN", "NOT_IN")

#: JSON as a constraint carries it.
Json = None | bool | int | float | str | list["Json"] | dict[str, "Json"]


class Constraint(Protocol):
    """One ``{key, operator, value}`` constraint."""

    @property
    def key(self) -> str: ...

    @property
    def operator(self) -> str | Enum: ...

    @property
    def value(self) -> Json: ...


def _operator(constraint: Constraint) -> str:
    operator = constraint.operator
    name = operator.value if isinstance(operator, Enum) else operator
    if name not in OPERATORS:
        raise ValueError(f"{name!r} is not a descriptor operator: one of {', '.join(OPERATORS)}.")
    return str(name)


def _listed(constraint: Constraint, operator: str) -> list[Json]:
    if not isinstance(constraint.value, list):
        raise ValueError(f"{operator} on {constraint.key} takes a list of values, got {constraint.value!r}.")  # noqa: TRY004 - a bad input, answered as one
    return constraint.value


def _number(constraint: Constraint, operator: str) -> int | float:
    value = constraint.value
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{operator} on {constraint.key} takes a number, got {value!r}.")  # noqa: TRY004 - a bad input, answered as one
    return value


def _any(conditions: Iterable[Q]) -> Q:
    """The disjunction of these, and nothing when there are none."""
    combined: Q | None = None
    for condition in conditions:
        combined = condition if combined is None else combined | condition
    return combined if combined is not None else Q(pk__in=[])


def constraint_q(constraint: Constraint, column: str = "stored_descriptors") -> Q:
    """One constraint against a stored descriptors column.

    Containment for everything that is an equality, so the GIN index serves it. A negation
    also asks that the key is there: a row that does not state the key is not a row that differs.
    """
    key, value, operator = constraint.key, constraint.value, _operator(constraint)
    present = Q(**{f"{column}__has_key": key})
    if operator in ("EQUALS", "MATCHES"):
        return Q(**{f"{column}__contains": {key: value}})
    if operator == "NOT_EQUALS":
        return present & ~Q(**{f"{column}__contains": {key: value}})
    if operator == "IN":
        return _any(Q(**{f"{column}__contains": {key: member}}) for member in _listed(constraint, operator))
    if operator == "NOT_IN":
        return present & ~_any(Q(**{f"{column}__contains": {key: member}}) for member in _listed(constraint, operator))
    if operator == "CONTAINS":
        return Q(**{f"{column}__contains": {key: [value]}})
    if operator == "EXISTS":
        if value not in (None, True, False):
            raise ValueError(f"EXISTS on {key} takes true or false, got {value!r}.")
        return ~present if value is False else present
    # jsonb compares two numbers as numbers, so the member is compared as it is stored.
    lookup = "gte" if operator == "GTE" else "lte"
    return present & Q(**{f"{column}__{key}__{lookup}": _number(constraint, operator)})


def requires_q(constraints: Sequence[Constraint], *, keys: Iterable[str], prefix: str = "") -> Q:
    """The rows satisfying every one of these constraints, among those whose structure declares ``keys``.

    ``prefix`` is the path to the described row, for a filter on a relation (``"dataset__"``).
    No constraints ask nothing.
    """
    declared = set(keys)
    condition = Q()
    for constraint in constraints:
        if constraint.key not in declared:
            raise ValueError(f"{constraint.key!r} is not a descriptor here: the structure declares {sorted(declared)}.")
        condition &= constraint_q(constraint, f"{prefix}stored_descriptors")
    return condition
