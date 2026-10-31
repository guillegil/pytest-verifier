"""The module the ``pytest_verifier`` entry point names: pytest registers it under that name, and
it loads the plugin through ``pytest_plugins``.

It is not the package itself so that editors can find the ``verify`` fixture here. jedi (VS Code's
Jedi mode, Spyder, python-lsp-server, jedi-language-server) looks for a plugin's fixtures in the
module its pytest11 entry point names, and accepts a fixture function defined there or imported
by a single-name ``from ... import`` line; it does not follow ``pytest_plugins``. In the package,
the name ``verify`` belongs to the deprecated alias of ``checks``.

The import only runs for static tools: importing the plugin here would import it before pytest
registers it, which pytest warns about.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

pytest_plugins = ["pytest_verifier.plugin"]

if TYPE_CHECKING:
    from .plugin import verify  # one name, no "as": jedi reads only that form
