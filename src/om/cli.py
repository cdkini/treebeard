"""CLI entry point for `om`.

Subcommands live in `om.commands` and are auto-registered at import time.
To add a new command, drop a module into `om/commands/` that defines a
`click.Command` (or `click.Group`) named `command`.
"""

from __future__ import annotations

import sys

import click

from om import __version__, usage_log
from om.commands import iter_commands


@click.group(
    help="om — the omniscience CLI.",
    context_settings={"help_option_names": ["-h", "--help"]},
)
@click.version_option(__version__, "-V", "--version", prog_name="om")
@click.pass_context
def cli(ctx: click.Context) -> None:
    """Root command group."""
    # `init` logs itself after creating the vault, since the vault
    # doesn't exist yet at this point. Other commands log here, before
    # dispatch, against the default config location.
    if ctx.invoked_subcommand and ctx.invoked_subcommand != "init":
        usage_log.log_invocation(None, sys.argv[1:])


for command in iter_commands():
    cli.add_command(command)


if __name__ == "__main__":
    cli()
