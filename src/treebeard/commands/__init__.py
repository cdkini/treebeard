"""Subcommand registry.

Each module in this package may export a top-level `command` attribute
(a `click.Command` or `click.Group`). `iter_commands()` discovers them
so `cli.py` can register them without hard-coding imports.
"""

from __future__ import annotations

import importlib
import pkgutil
from collections.abc import Iterator

import click


def iter_commands() -> Iterator[click.Command]:
    """Yield every `command` exported by a module in this package."""
    for module_info in pkgutil.iter_modules(__path__):
        if module_info.name.startswith("_"):
            continue
        module = importlib.import_module(f"{__name__}.{module_info.name}")
        command = getattr(module, "command", None)
        if isinstance(command, click.Command):
            yield command
