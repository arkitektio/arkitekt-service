# Migrations, jobs and upgrades

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
| `upgrade --from A --to B` | the contract's `upgrades`, for the majors the move crosses into | in an update, after `migrate`, only when the version changed and the release declares any |
| `superuser` | `createsuperuser --noinput`, from `DJANGO_SUPERUSER_*` | when an operator asks |
| the service's own | one `manage.py` command each, declared in `jobs=` | `setup` ones inside `migrate`; any of them when an operator asks (`konstruktor job run <service> <job>`) |

An update, for each service whose build changes: back up, fetch the new image, write its
config with the new image, **stop the service**, run `migrate`, run `upgrade`, and only then
recreate the container. If a step before the recreate fails, the files and builds are put back
and **the previous build is started again — on whatever the new migrations already did**. A
rollback does the same on purpose. Nothing undoes a migration.

Locally (`deployments/next`) a service runs `arkitekt-service standalone --debug`, which is
`run migrate` and then the development server: restart the container to apply a new migration.

## Rules

### Migrations

1. **A model change and its migration are one commit.** `python manage.py makemigrations
   --check --dry-run` must be clean; CI runs it.
2. **Never migrate, seed or repair at start, in `ready()`, or in a request.** It goes into a
   migration, a setup job or an upgrade.
3. **Within a major, a migration leaves a schema the previous release still runs on.** That is
   what makes a failed update and a rollback safe. In practice: add tables and columns (nullable
   or with a database default), add indexes; do not drop or rename a column, tighten a
   constraint the old code can violate, or change what a value means.
4. **What breaks rule 3 is a major**: `feat!:` with a `BREAKING CHANGE:` footer saying what
   changes. Split it where possible — add in one release, stop using, drop in the next major.
5. **A migration is done when it returns.** A data migration that takes minutes on a real hub,
   or that must not overlap with the old code writing, is an upgrade, not a `RunPython`.
6. **Migrations must run on an empty database and on a populated one**, and `migrate` must be
   safe to run again on a database that is already there (it is run for every build).

### Jobs

1. **Declare every command an operator or an installer should be able to run** in the
   contract: `jobs={"name": Job(("command", "--arg"), "What it does, in a line")}`. A
   `manage.py` command that is not declared cannot be run on a hub. A command nobody runs any
   more is deleted, not kept.
2. **A setup job** (named in `setup=`) is what the database needs before the service serves on
   it, from the config: an operator account, registered partners, seeded rows. It runs for
   **every build**, so it must change nothing when run again, must not need the service or its
   peers to be up, and must succeed when the config names nothing for it.
3. **Any job is safe to run again**, runs to an end (no loops, no prompts), and exits non-zero
   when it failed.
4. **Secrets come from the config or the environment, never from arguments.**
5. `migrate`, `plan`, `upgrade` and `superuser` are every service's and cannot be declared.
6. A one-off rewrite of existing data is not a setup job: it is an upgrade (once, on the way
   into a major) or, when it is safe beside a serving release, an operator job.

### Upgrades

1. An upgrade is a function, declared in the contract by the **major it leads to**:
   `upgrades={6: upgrades.into_six}`. It imports its models when called: the contract is read
   before the service has a config.
2. **A release that needs one is a major.** A move within a major runs none.
3. It runs with the service stopped, after `migrate`, and **must be safe to run twice**.
4. It fails loudly (raise): the installer then starts the previous build again.
5. When an upgrade is dropped, `Description(upgrade_from=…)` is raised to the oldest version
   that can still be moved from directly; the installer refuses older hubs by name.
6. A release that only works beside certain versions of a peer says so:
   `Description(requires={"rekuest": ">=6"})`.

## Checklist for a change that touches the database

- [ ] migration committed with the model change; `makemigrations --check --dry-run` clean
- [ ] the previous release still runs on the migrated schema — or the commit is `feat!:`
- [ ] new config-driven rows: a setup job, declared in `jobs=` and `setup=`, re-runnable
- [ ] existing rows rewritten: an upgrade into the next major, re-runnable, with a test
- [ ] new command: declared as a job with a summary, or not added
- [ ] the service's `CLAUDE.md` lists the job
- [ ] `arkitekt-service run migrate` twice against a real database: both exit 0
