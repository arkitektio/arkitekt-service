"""Collaborations: what a guest of an organization is let in to, and nothing else.

A full member puts objects of a service in a *collaboration* (:mod:`.items`); the coordination server
grants a guest the collaboration, with roles that hold only inside it (authentikate's ``collabs`` claim).
For a guest, every object the service hands out must be in one of their collaborations (:mod:`.guests`,
and the gate in :mod:`arkitekt_service.graphql`); what they may change, they may change only
there (``require_in_collaboration``).

What is in a collaboration:

- what was put in it, and what that needs to be seen (its structure's ``depends``: a scene's
  datasets, a dataset's stores), put in along with it;
- what is part of any of those, live: a row whose required, cascading foreign keys lead to one
  (a scene's layers, a dataset's arrays), whenever it was made.

A Django app: add ``"arkitekt_service.collaborations"`` to ``INSTALLED_APPS``. Needs authentikate >= 5
(the ``authentikate`` extra).
"""
