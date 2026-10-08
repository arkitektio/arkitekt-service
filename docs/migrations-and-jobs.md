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
- [ ] `tests/test_prepared.py` (`arkitekt_service.prepared`) passes: migrations committed, every job a command, setup re-runnable
