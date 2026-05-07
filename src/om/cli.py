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
    argv = sys.argv[1:]
    if not argv:
        return
    # Subcommands set ctx.obj["config_dir"] so logging targets the same
    # vault the command actually used (not just the default location).
    ctx.ensure_object(dict)
    ctx.call_on_close(lambda: usage_log.log_invocation(ctx.obj.get("config_dir"), argv))


for command in iter_commands():
    cli.add_command(command)


if __name__ == "__main__":
    cli()
