# arkitekt-service

What a service of an [Arkitekt](https://arkitekt.live) hub is made with. Its parts, each for one thing a service does:

| | |
|---|---|
| `arkitekt_service.contract` | What the service's **image** answers the hub's installer: what it needs, its own config written from the hub's facts, its migrations and jobs. |
| `arkitekt_service.service` | What the service **is** to the hub's rekuest: the structures it hosts and the signals it emits. |
| `arkitekt_service.hook` | What can be **done** in its process: a HookAgent, whose actions rekuest reaches over HTTP. |
| `arkitekt_service.feed` | What it **changed**, durably and in order: a change feed another service reads with its instance key. |
| `arkitekt_service.server` | What every service is as a **Django server**: the settings blocks they all spell alike, `ensureadmin`, `validate_settings`. |
| `arkitekt_service.testing` | A pytest fixture and check that **prove an image** in a hub made for the test. |

They share `arkitekt_service.trust` — no secrets between services: every instance signs with
its own key, and the hub's trust bundle lists the public halves. A service is named by its name
in its hub (`mikro`, `rekuest`); every service token also carries the hub's uuid (`hub`, from
`AUTHENTIKATE["hub"]`), and a service refuses a token of another hub. A process uses any of
them without the others.

```
pip install arkitekt-service
```

## The contract

Everything that is done with a service's image goes through one command in it:

```
arkitekt-service                      # what it is, needs and offers, and what to run in it (JSON)
arkitekt-service serve                # become the service: serve, and nothing else
arkitekt-service debug                # the same, with the development server
arkitekt-service run migrate          # bring its database to this release, then its setup
arkitekt-service run <job>            # any other job it offers: plan, superuser, its own
arkitekt-service standalone           # run migrate, then serve: one image, run on its own
arkitekt-service render               # this release's config, from the hub's facts
arkitekt-service check                # does this release read a config as written?
arkitekt-service label                # the description as an image label, for the build
```

An installer (Konstruktor) then needs to know the hub, and nothing about a service that the
service's image does not say. How a release spells its config, which keys it renamed, how it
is started, what it has to do to its data — all of that ships in the image, with the code it
belongs to. Exit code `78` is a release's own refusal (facts it cannot be configured from, a
setting it does not read, a job it does not offer), with the reason on stderr.

### The image says what it is

The image's own command is the first of these:

```dockerfile
CMD ["arkitekt-service", "describe"]
```

So an installer asks by running the image with no command, and assumes nothing of what is
inside: every other command it runs is one the answer named. A service written in another
language takes part by printing the same description as its own command. Whoever starts the
image by hand gets the description too, and beside it, on stderr, a note saying that nothing
is being served and what to run instead.

Running it means pulling the image first, gigabytes before anyone has agreed to anything. So
the release build stamps the same description into the image as a label,
`live.arkitekt.service`, and an installer reads that from the registry — the image's config,
a few kilobytes — and runs the image only when it has no label. `arkitekt-service label`
prints it on one line, in the service's checkout, for `docker/build-push-action`:

```yaml
- id: describe
  uses: arkitektio/arkitekt-service/.github/actions/describe-label@v7.1.2
  with:
    service: mikro_server.contract   # the image's ARKITEKT_SERVICE
- uses: docker/build-push-action@v5
  with:
    labels: |
      ${{ steps.meta.outputs.labels }}
      ${{ steps.describe.outputs.label }}
```

What the description says:

- `name`: what a hub calls the service: lowercase letters, digits and underscores, no hyphen.
- `identifier`: what the service is registered as, and what a client asks for.
- `needs`, `offers`, `requires`: what a hub has to provide, and what other services are
  wired to.
- `needs.databases`: the databases it wants in the hub's Postgres, by name (`main` unless it
  says otherwise). A hub calls each `<service>_<name>` and hands it over under the name the
  service gave: `blocks.postgres(facts, "events")`. A name is one Postgres takes unquoted
  (a lowercase letter, then lowercase letters, digits and underscores); anything else is
  refused when the service is declared.
- `serve` and `debug`: what a container of the image runs to serve, in production and in
  development. An installer writes the one that applies as the service's command.
- `jobs`: what can be run in the image beside its start, by name, each with the command that
  runs it. `migrate` is every service's, and lists the jobs it runs as its setup
  (`includes`). An operator runs any of them again by name, e.g. `konstruktor job run mikro
  ensureadmin`, which is `arkitekt-service run ensureadmin` in a container of the image.
- `prepare`: which job brings the database to the release (`migrate`).
- `render`: what writes the release's config from the hub's facts.
- `hosts`: the structures the service holds and the signals it sends about them, as data
  (see below). A hub's installer hands this to the hub's rekuest.
- `source`: where the image's code came from (repository, commit) and where it sits in the
  image, so that an installer can run the service from a checkout. The build says it:
  `ARKITEKT_SOURCE_REPOSITORY` and `ARKITEKT_SOURCE_REVISION` in the image's environment.
- `sidecars`: what runs beside the service as an image of its own: one it does not run
  without (rekuest's takt, named from the service's image), or an `optional` one it drives
  on a hub that has the use for it (Lok's mesh control server).

### A container serves; a job prepares

A service's container does one thing: serve. `arkitekt-service serve` becomes the server and
does nothing before that. Everything its database needs first is `run migrate`: wait for the
database, apply the release's migrations, then run the jobs the service named as its `setup`
(an admin account, seeded rows), all in one process. Whoever starts the service runs it as a
job of its own, once per build, before the first start and before an update's:

```
docker compose run --rm --no-deps mikro arkitekt-service run migrate
```

or, in a compose file that has no installer, as a service the server waits for:

```yaml
mikro-migrate:
  image: jhnnsrs/mikro:7
  command: arkitekt-service run migrate
mikro:
  image: jhnnsrs/mikro:7
  command: arkitekt-service serve
  depends_on:
    mikro-migrate:
      condition: service_completed_successfully
```

A container that merely restarts then does none of it.

`arkitekt-service standalone` is the two together, for the one case where that is what is
wanted: a single image run on its own — a developer's compose file, a quick look at a
release. It prepares, then serves, and serves nothing if preparing failed. Nothing that
manages a stack uses it.

### Declaring a service

A service declares itself in one module, named by `ARKITEKT_SERVICE` in its Dockerfile:

```python
from arkitekt_service.contract import JSON, Contract, Description, Facts, Job, Needs, Offers, Start, blocks


def render(facts: Facts) -> dict[str, JSON]:
    document = blocks.server(facts)  # django, postgres, redis, authentikate
    document["datalayer"] = blocks.datalayer(facts, "media", "zarr")
    return document


contract = Contract(
    description=Description(name="mikro", identifier="live.arkitekt.mikro", needs=Needs(storage=["media", "zarr"])),
    settings=Settings,
    render=render,
    # How it is started. There is no script beside this: `arkitekt-service serve` becomes it.
    serve=Start(("daphne", "-b", "0.0.0.0", "-p", "80", "mikro_server.asgi:application")),
    debug=Start(("python", "manage.py", "runserver", "0.0.0.0:80")),
    # What else can be run in the image, by name, and which of it `migrate` runs as setup.
    jobs={"ensureadmin": Job(("ensureadmin",), "Create the operator account the config names")},
    setup=("ensureadmin",),
)
```

The rules a migration and a job are held to, so that an installer can update a
running hub and put it back, are in [docs/migrations-and-jobs.md](docs/migrations-and-jobs.md).
What a service's scopes and roles are, and how every field asks for them, is in
[docs/scopes-and-roles.md](docs/scopes-and-roles.md).

The hub's facts (`arkitekt_service.contract.facts`) and a service's description
(`arkitekt_service.contract.description`) are versioned documents; an image refuses facts it
does not understand rather than dropping them.

## What a service hosts

Said once, as data, in the contract — so a hub knows it from the image, before the service runs:

```python
from arkitekt_service.contract import Descriptor, Hosts, Signal, Structure

HOSTS = Hosts(
    structures=[Structure(identifier="@mikro/arraydataset", label="Array Dataset",
                          descriptors=[Descriptor(key="@mikro/n_channels", type="INT")])],
    signals=[Signal(identifier="@mikro/arraydataset", kinds=["CREATED", "UPDATED", "DELETED"],
                    descriptors=["@mikro/n_channels"])],
)
contract = Contract(description=Description(name="mikro", ..., hosts=HOSTS), ...)
```

and bound to the models and the code where Django is loaded:

```python
from arkitekt_service.service import Service, organization_of
from arkitekt_service.hook import HookAgent

service = Service("mikro", hosts=contract.description.hosts)
dataset = service.structure(ArrayDataset, "@mikro/arraydataset", describe=array_descriptors)
service.model_signal(dataset, organization=organization_of())

agent = HookAgent("mikro", description="mikro's housekeeping")


@agent.action
def reembed_stale(organization: str) -> dict:
    """Re-embed stale rows."""
    ...


urlpatterns = [..., *service.urls, *agent.urls]
```

A structure the contract does not declare cannot be bound, and a service whose contract declares
one that nothing binds does not start: what the image says and what the service holds cannot
drift apart. The hub's rekuest catalogues what the installer handed it; the service still
answers the same at `_rekuest/service/manifest`, for a rekuest nobody told.

A service says what exists; an agent says what can be done. Neither knows the other.

An action may take an object of a structure, so that a trigger on that structure's signals can
run it: the port is declared, and the run is handed a `StructureRef` (None for a nullable port a
schedule's run left out):

```python
from arkitekt_service.hook import StructurePort, StructureRef


@agent.action(args=[StructurePort("metric", "@mikro/metric", nullable=True)])
def ingest_metrics(organization: str, metric: StructureRef | None = None) -> dict:
    """Ingest new metrics."""
    ...
```

## A change feed

Signals are best effort: a queue in the process, dropped when full, gone when it dies. A feed is
what another service can rely on. The producer records each change in the transaction that makes
it, and serves the feed to the services it names:

```python
from arkitekt_service.feed.api import Change, record_change
from arkitekt_service.feed.publisher import FeedPublisher

with transaction.atomic():
    metric = Metric.objects.create(...)
    record_change(Change("@mikro/metric", "CREATED", str(metric.pk), org.slug, uuid=metric.uuid))

publisher = FeedPublisher("mikro")
publisher.feed("@mikro/metric", readers=["kraph"], render=render_metrics)
urlpatterns = [..., *publisher.urls]  # POST _feed, signed by the reader's instance key
```

A reader applies one organization's pages, each in one transaction with its cursor — nothing
lost, nothing applied twice, one run at a time:

```python
from arkitekt_service.feed.consumer import apply_pages
from arkitekt_service.feed.reader import FeedReader

reader = FeedReader(url=mikro_feed_url, producer="mikro", feed="@mikro/metric", issuer="kraph")
apply_pages(reader, "lab", apply=apply_page, budget_s=20)
```

Entries come in the order of the transactions that wrote them, and only once no open transaction
can still commit below them (`xid < pg_snapshot_xmin(pg_current_snapshot())`) — so an open
transaction anywhere in the cluster holds the feed back while it is open. A reader stops for good
(until `resume`) when the producer was restored (its epoch changed, or its transaction counter
went back) or when an entry cannot be applied (`Poison`); any other error fails the run and the
next one retries the same page. Add `"arkitekt_service.feed"` to `INSTALLED_APPS`.

### Descriptors are stored

A model whose structure declares descriptors keeps them on its rows. Binding it refuses any
model that is not a `Described`:

```python
from django.contrib.postgres.indexes import GinIndex
from arkitekt_service.service.described import Described


class ArrayDataset(Described):
    ...

    class Meta:
        indexes = [GinIndex(fields=["stored_descriptors"], name="mikro_ds_desc_gin", opclasses=["jsonb_path_ops"])]
```

Every save recomputes `stored_descriptors`, a save with `update_fields` included; a new row is
described right after its insert, so a describer may read its relations. Signals and
`service.describe(obj)` read the stored value, and so does a port's `requires`, which is one
indexed condition:

```python
rows = ArrayDataset.objects.filter(service.requires(ArrayDataset, constraints))
```

A save does not see everything, so the writer restores the row:

- rows written after the object that a descriptor counts: `service.restore(obj)`. Inside the
  transaction that created the object this adds no UPDATED: its CREATED carries what is stored
  at the commit
- many-to-many changes: `service.restore(obj)`
- `update()`: `service.restore(queryset)`
- `bulk_update`: `service.stamp(rows)` first, with `stored_descriptors` among the fields

A describer that raises keeps the row's previous value. The setup job `restore_descriptors`
(declare it in `jobs=` and `setup=`) stores what any row still owes; `--all` describes every
row again after a key changed its meaning.

## Collaborations: what a guest is let in to

A guest of an organization sees nothing of it but the *collaborations* granted to them, with roles that
hold only inside each (the coordination server lists them in the token's `collabs`; authentikate
reads it). A collaboration lives in the service that holds its objects:
`INSTALLED_APPS += ["arkitekt_service.collaborations"]` (needs the `authentikate` extra).

What is in a collaboration is data: a full member puts objects in (`collaborations.items.add(collaboration, scene)`, or
the `addToCollaboration` mutation of `arkitekt_service.collaborations.graphql`). With each comes what its
structure says it needs to be seen, and what is part of any of them (a row whose required,
cascading foreign keys lead to one) is in the collaboration live:

```python
service.structure(models.Scene, "@mikro/scene", depends=lambda scene: [scene.world, *lenses_of(scene)])
```

For a guest, every read is held to their collaborations:

- at a root field, the scoping chokepoint narrows the queryset
  (`object_scope(queryset, info=info)`; nested querysets are left as they are, so the optimizer
  plans the same for guests and members);
- the gate (`arkitekt_service.graphql.CollaborationGate`, after authentikate's extension) drops whatever a
  resolver hands out that is in none of their collaborations, and refuses root fields that hand out no
  objects (`COLLABORATION_DOES_NOT_REACH`);
- a mutation opened to guests (`AuthExtension(..., allow_scoped=True)`) checks its target:
  `require_in_collaboration(target, any_role_of=["editor"])`.

`annotate_schema(schema, service)` adds `@identified(by: "@mikro/scene")` to each structure's type
and, behind the gate, says the service lets guests in. The instance a guest's collaborations are read
for is the service's `AUTHENTIKATE.audience`, which konstruktor renders as its instance id.

## A Django server like the others

```python
INSTALLED_APPS = [..., "arkitekt_service.server"]
```

```python
from arkitekt_service.server.settings import DjangoSettings, PostgresSettings, RedisSettings, ServiceSettings


class Settings(ServiceSettings):  # the config file, with the environment over it
    django: DjangoSettings
    postgres: PostgresSettings
    redis: RedisSettings
    ...  # and what is the service's own
```

brings `manage.py ensureadmin` (the operator account the config names), `manage.py
validate_settings [--strict]` (the config as the release reads it, secrets masked) and a check
that warns, when `migrate` runs, about keys the release does not read.

## Proving an image

```python
# tests/conftest.py
pytest_plugins = ["arkitekt_service.testing"]


# tests/test_hub.py
@pytest.mark.hub
def test_the_image_runs_in_a_hub(service_hub):
    check_service(service_hub)
```

`pytest -m hub --service-image mikro:dev` starts a hub with nothing but a coordination server
and that image, as an installer would, and asks of it what every service has to answer: ready,
healthy, closed to nobody, open to an app the hub issued a token to, fully migrated, never
restarted. It needs Docker and `konstruktor`; it is for the job that builds the image.

## Development

```
uv sync
uv run pytest                                          # the contract and the service
uv run pytest tests/hook --ds=hook_project.settings    # the hook agent: a process that is no service
uv run pytest tests/feed --ds=feed_project.settings    # the change feed: a real Postgres, via dokker
uv run basedpyright
```

Releases are cut from conventional commits on `main` (tag-only, like the other Arkitekt
packages) and uploaded to PyPI.
