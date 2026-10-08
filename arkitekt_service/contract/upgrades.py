"""What a service does to its own data when a deployment moves from one version to another.

Schema changes are Django migrations, and those run in the ``migrate`` job, before the server
starts. This is for what a migration cannot express, or must not do while the previous release
still serves: a rewrite that needs the old and the new code not to overlap, a one-off pass over
rows another program also writes. An installer runs it once, between stopping the old server
and starting the new one::

    arkitekt-service run upgrade --from 5.2.0 --to 6.0.0

A service declares its upgrades in its contract, keyed by the **major each leads to**::

    Contract(..., upgrades={6: upgrades.into_six, 7: upgrades.into_seven})

A move runs the ones it crosses into: from 5.x to 6.x runs ``upgrades[6]``; from 5.x to 7.x
runs ``upgrades[6]`` and then ``upgrades[7]``. A move within a major runs nothing, and neither
does going back. An upgrade has to be safe to run twice: an installer that stops half way runs
it again.

A contract is read before the service has a config (``describe``), so an upgrade imports its
models when it is called, not where it is defined.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping

#: One upgrade: what has to happen to the data on the way into a major.
Upgrade = Callable[[], None]


def major(version: str) -> int:
    """The major of a version as release tags spell it: ``6.0.0``, ``6.1.0-rc.1``, ``v6``."""
    try:
        return int(version.strip().removeprefix("v").split(".")[0])
    except ValueError:
        raise ValueError(f"{version!r} is not a version (expected something like 6.0.0)") from None


def crossed(upgrades: Mapping[int, Upgrade], left: str, reached: str) -> list[int]:
    """The majors with an upgrade that a move from ``left`` to ``reached`` crosses into, in order."""
    start, end = major(left), major(reached)
    return [step for step in sorted(upgrades) if start < step <= end]


def run(upgrades: Mapping[int, Upgrade], left: str, reached: str) -> list[int]:
    """Run every upgrade the move crosses, oldest first; the majors that had one."""
    steps = crossed(upgrades, left, reached)
    for step in steps:
        upgrades[step]()
    return steps
