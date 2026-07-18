"""CLI command submodules.

Each command is a module with a single public function ``run(...)``
that accepts the parsed CLI arguments, calls a service, formats
output, and returns an exit code.

Conventions
-----------
- ``run(**kwargs)`` accepts the kwargs that the Typer wrapper binds.
- A ``--json`` flag, when set, makes ``run`` print a JSON document
  on stdout and return 0 (or non-zero on error). Otherwise it prints
  human-friendly text via Rich.
- Commands return ``0`` (success), ``1`` (warning), or ``2`` (failure).
"""

from . import monthly_report