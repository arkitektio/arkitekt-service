"""A job a service suggests be run by itself: its schedule, how long it may take, what it owes."""

import dataclasses
import json

import pytest

from arkitekt_service.contract import Job
from arkitekt_service.contract.schedule import check
from tests.contract import example


@pytest.mark.parametrize(
    "schedule",
    [
        "hourly",
        "daily 03:00",
        "daily 01:00-06:00",
        "weekly sun 02:00-05:00",
        "daily 23:30-01:00",
    ],
)
def test_a_schedule_is_read(schedule: str) -> None:
    assert check(schedule) == schedule


@pytest.mark.parametrize(
    "schedule",
    [
        "",
        "nightly",
        "daily 3:00",
        "daily 24:00",
        "daily 03:00-",
        "weekly sunday 02:00",
        "*/5 * * * *",
    ],
)
def test_anything_else_is_refused_in_words(schedule: str) -> None:
    with pytest.raises(ValueError, match="is not a schedule"):
        check(schedule)


def test_a_scheduled_job_says_all_of_it_to_the_installer() -> None:
    contract = dataclasses.replace(
        example.contract,
        jobs={
            "digest": Job(
                ("digest_files",),
                "Hash what is owed",
                schedule="daily 01:00-06:00",
                long_running=True,
                backlog=("--backlog",),
                deadline="--until",
            ),
            "purge": Job(("purge",), "Free deleted bytes", schedule="daily 03:00"),
        },
    )

    said = json.loads(contract.said().model_dump_json())["jobs"]

    assert said["digest"] == {
        "command": ["arkitekt-service", "run", "digest"],
        "summary": "Hash what is owed",
        "includes": [],
        "schedule": "daily 01:00-06:00",
        "long_running": True,
        "backlog": ["arkitekt-service", "run", "digest", "--backlog"],
        "deadline": "--until",
    }
    assert said["purge"]["schedule"] == "daily 03:00"
    assert said["purge"]["backlog"] is None


def test_a_job_that_may_take_hours_is_never_part_of_the_setup() -> None:
    with pytest.raises(ValueError, match="may take hours"):
        dataclasses.replace(
            example.contract,
            jobs={"digest": Job(("digest_files",), long_running=True)},
            setup=("digest",),
        )


def test_a_schedule_the_grammar_does_not_read_is_refused_where_it_is_written() -> None:
    with pytest.raises(ValueError, match="job `purge`.*is not a schedule"):
        dataclasses.replace(
            example.contract, jobs={"purge": Job(("purge",), schedule="nightly")}
        )
