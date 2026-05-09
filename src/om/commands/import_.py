"""`om import <integration>` — pull notes/artifacts from external integrations.

Group is extensible: add a new integration by registering a subcommand on
`command` (e.g. `@command.command("foo")`).
"""

from __future__ import annotations

import click


@click.group("import")
@click.option(
    "--force",
    is_flag=True,
    default=False,
    help="Force re-querying artifacts from the integration, ignoring any cache.",
)
@click.pass_context
def command(ctx: click.Context, force: bool) -> None:
    """Import artifacts from an external integration into the vault."""
    ctx.ensure_object(dict)["force"] = force


@command.command("granola")
@click.pass_context
def granola(ctx: click.Context) -> None:
    """Import notes from Granola."""
    raise NotImplementedError
