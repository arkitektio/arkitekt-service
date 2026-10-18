"""What a service of an Arkitekt hub is made with.

Three parts, for three different things a service process does:

:mod:`arkitekt_service.contract`
    What its *image* answers the hub's installer, before and around the service running: what
    it needs, its own config written from the hub's facts, its migrations and jobs.
    ``arkitekt-service <verb>``.

:mod:`arkitekt_service.service`
    What the service *is* to the hub's rekuest: the structures it hosts and the signals it
    emits.

:mod:`arkitekt_service.hook`
    What can be *done* in its process: a HookAgent, whose actions rekuest reaches over HTTP.

They share :mod:`arkitekt_service.trust` — no secrets between services: every instance signs
with its own key, and the hub vouches for the public halves — and nothing else. A process uses
any of them without the others; only ``contract`` is imported without Django.
"""
