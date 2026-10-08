"""What every service of a hub is, as a Django server: the parts each one used to copy.

A Django app, added to a service's ``INSTALLED_APPS``::

    INSTALLED_APPS = [..., "arkitekt_service.server"]

It brings

- the settings blocks every service spells the same way (:mod:`arkitekt_service.server.settings`):
  ``django``, ``postgres``, ``redis``, ``instance``, and a base for the service's ``Settings``
  that reads them from the config file and the environment;
- ``manage.py ensureadmin``: the operator account the config names;
- ``manage.py validate_settings``: the config as this release reads it, secrets masked, and what
  it says that the release does not read (``--strict`` fails on that);
- ``manage.py upgrade``: the upgrades the service's contract declares, between two versions;
- a system check, run by ``migrate``, that warns about the same.

The commands and the check find the service's settings through its contract
(``ARKITEKT_SERVICE``), so there is nothing to configure.
"""
