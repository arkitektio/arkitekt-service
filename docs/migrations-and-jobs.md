# Migrations and jobs

How a service changes its database so that a hub's installer (konstruktor) can move a running
hub to the new release, and put it back when that fails. Written for whoever changes a
service, person or agent. The service's own `CLAUDE.md` lists what that service declares.

## What an installer does with a release

A service's container **only serves**. Nothing is migrated at start. Everything the database
needs is a job the image offers by name, and the installer runs it in a container of its own:

| Job | What it is | When the installer runs it |
| --- | --- | --- |
| `migrate` | wait for the database, `manage.py migrate --noinput`, then the service's `setup` jobs in order | once per **build**: before the first start, and in an update before anything is recreated |
| `plan` | `manage.py migrate --plan` | `konstruktor update --check`, to show what an update would apply |
| `superuser` | `createsuperuser --noinput`, from `DJANGO_SUPERUSER_*` | when an operator asks |
| the service's own | one `manage.py` command each, declared in `jobs=` | `setup` ones inside `migrate`; any of them when an operator asks (`konstruktor job run <service> <job>`) |

An update, for each service whose build changes: back up, fetch the new image, write its
config with the new image, **stop the service**, run `migrate`, and only then recreate the
container. If a step before the recreate fails, the files and builds are put back
and **the previous build is started again — on whatever the new migrations already did**. A
rollback does the same on purpose. Nothing undoes a migration.

Locally (`deployments/next`) a service runs `arkitekt-service standalone --debug`, which is
`run migrate` and then the development server: restart the container to apply a new migration.

## Rules

### Migrations

1. **A model change and its migration are one commit.** `python manage.py makemigrations
   --check --dry-run` must be clean; the service's `tests/test_prepared.py` asserts it.
2. **Never migrate, seed or repair at start, in `ready()`, or in a request.** It goes into a
   migration or a job.
3. **Within a major, a migration leaves a schema the previous release still runs on.** That is
   what makes a failed update and a rollback safe. In practice: add tables and columns (nullable
   or with a database default), add indexes; do not drop or rename a column, tighten a
   constraint the old code can violate, or change what a value means.
4. **What breaks rule 3 is a major**: `feat!:` with a `BREAKING CHANGE:` footer saying what
   changes. Split it where possible — add in one release, stop using, drop in the next major.
5. **A rewrite of existing rows that needs only the database is a data migration**
   (`RunPython`). It runs exactly once per database, with the service stopped, and
   `konstruktor update --check` shows it. It uses the historical models (`apps.get_model`),
   not the service's code, so that it still runs when that code has moved on. A large one is
   `atomic = False` and works in batches, so that it holds no transaction open and can be
   run again after a failure.
6. **Migrations must run on an empty database and on a populated one**, and `migrate` must be
   safe to run again on a database that is already there (it is run for every build).

### Jobs

1. **Declare every command an operator or an installer should be able to run** in the
   contract: `jobs={"name": Job(("command", "--arg"), "What it does, in a line")}`. A
   `manage.py` command that is not declared cannot be run on a hub. A command nobody runs any
   more is deleted, not kept.
2. **A setup job** (named in `setup=`) is what the database needs before the service serves on
   it, from the config: an operator account, registered partners, seeded rows. It runs for
   **every build**, so it must change nothing when run again, must not need the service itself
   or another service to be up (the database, and what the config names — storage — are
   there), and must succeed when the config names nothing for it.
3. **Any job is safe to run again**, runs to an end (no loops, no prompts), and exits non-zero
   when it failed.
4. **Secrets come from the config or the environment, never from arguments.**
5. `migrate`, `plan` and `superuser` are every service's and cannot be declared.
6. **A rewrite that needs the service's current code, or something beside the database**
   (object storage, another service) is not a migration: a migration runs on every fresh
   database and in every test run, where neither is there. It is a job that finds what is
   still owed, does it, and does nothing when nothing is owed — so that it is safe on a new
   hub and safe to run again. Listed in `setup=` it runs for every build, before the service
   serves; that is the default. Only when it is too slow to wait for, or needs another service to be up, is it left out of the
   setup and run by an operator, and then the release reads both the old and the new shape
   until it has run.
7. **There is no separate upgrade step.** Nothing is keyed to a version: what has to happen to
   a database is said by its migrations and its setup, which is why a hub's installer, a
   developer's `standalone` container and a test database all end up the same.
8. **A job that deletes data is a dry run unless it is told `--yes`**, and is never in
   `setup=`. Its arguments follow `--`: `konstruktor job run <service> <job> -- --yes`. Every
   service that installs `arkitekt_service.server` has one to declare:
   `"prune_organizations": Job(("prune_organizations",), "...")` deletes all data of the
   organizations named (`--only`) or of every one not named (`--keep`), and keeps the
   organizations, their members and the change feed. It needs no knowledge of the service's
   models (`arkitekt_service.server.prune`): a row another organization's data holds on to
   is left and named. A service whose deletes owe something elsewhere (a feed entry its
   writers record) does it from the `pruning` signal.

### Scheduled jobs

A job can say when it should run by itself, and how it behaves when it does. All of it is a
suggestion to the installer: the operator turns a schedule on or off and may change when it
runs. konstruktor runs them; nothing in a service keeps a timer.

```python
"digest_bigfiles": Job(
    ("digest_bigfiles",),
    "Hash the stored files whose digest is owed",
    schedule="daily 01:00-06:00",  # hub-local time; done by 06:00
    long_running=True,             # may take hours: warned about, never in `setup=`
    backlog=("--backlog",),        # what is owed, from the database alone
    deadline="--until",            # told the window's end; starts no new work after it
),
```

1. **A schedule is `hourly`, `daily HH:MM[-HH:MM]` or `weekly <mon..sun> HH:MM[-HH:MM]`,** in
   the hub's local time. The part after `-` is the window: a hub that was off at the start
   still runs the job while the window is open, and without an end it is one hour. Anything
   else is refused where the contract is written.
2. **A scheduled job is safe to run on a serving hub, again and again.** It finds what is owed,
   does it, and does nothing when nothing is. One that deletes data still needs `--yes` and is
   therefore never scheduled.
3. **`long_running` jobs are never in `setup=`**: a hub waits for its setup before it serves.
4. **A backlog answers from the database alone, quickly,** and ends its output with one JSON
   line holding an `items` count (`{"items": 312, "bytes": 1800000000000}`). A scheduled run
   whose backlog owes nothing is skipped. `prepared.backlogs_answer(contract)` checks it.
5. **A job that takes a `deadline` stops starting work at it** and exits 0 with what is left
   still owed, so the next window picks it up. One that cannot stop part-way declares none.

### Versions

1. A release that only works beside certain versions of a peer says so:
   `Description(requires={"rekuest": ">=6"})`.
2. When migrations are squashed, or a setup job that converged old data is deleted,
   `Description(upgrade_from=…)` is raised to the oldest version that can still be moved from
   directly; the installer refuses older hubs by name.

## Checklist for a change that touches the database

- [ ] migration committed with the model change; `makemigrations --check --dry-run` clean
- [ ] the previous release still runs on the migrated schema — or the commit is `feat!:`
- [ ] new config-driven rows: a setup job, declared in `jobs=` and `setup=`, re-runnable
- [ ] existing rows rewritten: a data migration (database only), or a re-runnable job in `setup=` — with a test
- [ ] new command: declared as a job with a summary, or not added
- [ ] the service's `CLAUDE.md` lists the job
- [ ] a scheduled job: re-runnable on a serving hub, its backlog answers (`prepared.backlogs_answer`), a deadline honoured if declared
- [ ] `tests/test_prepared.py` (`arkitekt_service.prepared`) passes: migrations committed, every job a command, setup re-runnable
