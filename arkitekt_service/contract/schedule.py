"""When a job is due, as a service suggests it and an operator may change it.

One small grammar, read the same way here and by the installer that runs the schedule
(konstruktor's ``schedule`` module), so that what a contract says is what a hub does::

    hourly                     every hour, at the full hour
    daily 03:00                every day at 03:00
    daily 01:00-06:00          every day at 01:00, and it must be done by 06:00
    weekly sun 02:00-05:00     Sundays at 02:00, done by 05:00

Times are the hub's local time. The part after ``-`` is the run's *window*: a hub that was
off at the start still runs the job when it comes back while the window is open, and a job
that takes a deadline (:attr:`arkitekt_service.contract.Job.deadline`) is told the window's
end and stops starting new work there. Without an end a window is one hour.
"""

from __future__ import annotations

import re

DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")

_TIME = r"(?:[01]\d|2[0-3]):[0-5]\d"
GRAMMAR = re.compile(
    rf"hourly|daily (?P<daily>{_TIME})(?:-(?P<daily_end>{_TIME}))?"
    rf"|weekly (?P<day>{'|'.join(DAYS)}) (?P<weekly>{_TIME})(?:-(?P<weekly_end>{_TIME}))?"
)


def check(schedule: str) -> str:
    """``schedule`` if the grammar reads it, refused in words otherwise.

    Raises:
        ValueError: If it is not ``hourly``, ``daily HH:MM[-HH:MM]`` or ``weekly <day> HH:MM[-HH:MM]``.
    """
    if not GRAMMAR.fullmatch(schedule):
        raise ValueError(
            f"`{schedule}` is not a schedule: say `hourly`, `daily HH:MM`, `daily HH:MM-HH:MM` or "
            f"`weekly <{'|'.join(DAYS)}> HH:MM[-HH:MM]`, in the hub's local time"
        )
    return schedule
