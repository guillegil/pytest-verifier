"""``python -m pytest_verifier``: the ``pytest-verifier`` command (see :mod:`._cli`)."""
import sys

from ._cli import main

sys.exit(main())
