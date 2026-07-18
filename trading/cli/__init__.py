"""CLI subpackage.

Thin command layer on top of the existing trading engine. The CLI
contains NO trading logic — it parses args, calls services, formats
output, and returns exit codes. The trading engine is the source of
truth.
"""
