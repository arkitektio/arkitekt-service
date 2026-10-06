"""``python -m arkitekt_service <verb>``: the same as the ``arkitekt-service`` command, for where there is none."""

import sys

from arkitekt_service.contract.cli import main

sys.exit(main())
