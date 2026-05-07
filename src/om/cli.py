"""CLI entry point for `om`.

Subcommands live in `om.commands` and are auto-registered at import time.
To add a new command, drop a module into `om/commands/` that defines a
`click.Command` (or `click.Group`) named `command`.
"""

from __future__ import annotations

import click

from om import __version__
from om.commands import iter_commands


@click.group(
    help="om — the omniscience CLI.",
    context_settings={"help_option_names": ["-h", "--help"]},
)
@click.version_option(__version__, "-V", "--version", prog_name="om")
def cli() -> None:
    """Root command group."""


for command in iter_commands():
    cli.add_command(command)


if __name__ == "__main__":
    cli()
