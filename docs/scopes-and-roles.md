# Scopes and roles

What a request may do at a service is decided twice:

- the **role** says what the *person* may do: a member of the organization holds it, handed out by
  its admins (or by lok itself, see below);
- the **scope** says what the *app* acting for them may do: the user grants it when they approve the
  app.

A request passes when both hold.

## Declaring them

A service declares both in its contract, as its own (`Needs(scopes=..., roles=...)`):

```python
SCOPES = [
    Scope(key="mikro_read", description="See images, datasets, scenes, lenses, tables, annotations and their files."),
    Scope(key="mikro_write", description="Upload and change images, datasets, scenes, lenses, charts, layers, folders, transformations and metrics."),
    Scope(key="mikro_all", description="Do everything in Mikro on your behalf.", is_all_scope=True),
]
ROLES = [
    Role(key="mikro_admin", description="Full control of Mikro in this organization.", is_admin_role=True),
    Role(key="mikro_editor", description="Upload and change images and datasets, and share them.", is_default_role=True),
    Role(key="mikro_annotator", description="Annotate images, also as a guest in a collaboration."),
]
```

The description refuses anything else:

- Every key is the service's own, `<name>_<what>`: the coordination server keeps one row of a key per
  organization, whichever service declared it, so a bare `read` or `admin` would be shared by all.
- When a service declares scopes, exactly one is its **all-scope** (`is_all_scope`): it passes every
  scope check here. When it declares roles, exactly one is its **admin role** (`is_admin_role`): it
  passes every role check here. At most one role is its **default role** (`is_default_role`).
- Every description is a full sentence, written for the screens that show it: the user approving an
  app, the admin handing out a role. Say what the holder can see or do, and say when it reaches
  outside the hub or costs something (sending mail, spending a budget, approving a release).

What lok does with them: it registers them when the hub enrols, keeps descriptions and flags as the
service says them now and removes what no service declares any more; it gives every admin role to
the organization's owner and admins, and every default role to every full member (an admin may take
a default role away; lok does not give it back). Guests hold no roles: what a guest may do is what
their collaborations hold.

## Asking for them

Every root field of the schema says which scope an app needs for it, and a write which roles a
member needs:

```python
strawberry_django.mutation(extensions=[AuthExtension(scopes=["mikro_write"], any_role_of=["mikro_editor"])])
```

Name the specific scope and role, never the catch-alls: `annotate_schema` tells authentikate which
of the contract's scopes and roles stand for all of them. The directive publishes the requirement as
`@auth(required_scopes: [...], any_role_of: [...])`, which is what a generated client reads: a
turms `client_fields` entry on the client plugin collects the scopes of every root field the
client's operations select onto the generated client class (`MikroApi.__scopes__`), arkitekt-spec
reads them off the class a service returns, and every app using it asks for them. Nobody writes an
app's scopes by hand:

```yaml
client_fields:
  - name: __scopes__
    kind: tuple[str, ...]
    collector: {type: root_field_directive, directive: auth, argument: required_scopes}
    combine: union
```

Test that nothing is left out with `scope_gaps(schema) == []` (`arkitekt_service.graphql`): it lists
the root fields that ask for no scope, or for one the contract does not declare.

`AUTHENTIKATE.enforce_scopes` (on by default) can switch the runtime scope check off while apps catch
up; the directive is published either way.
