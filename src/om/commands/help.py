"""`om help` — canonical help surface.

Mirrors `om --help`. Both work; docs nudge users to the subcommand form
so bare `om` is unambiguously the picker.
"""

from __future__ import annotations

import click


@click.command("help")
@click.pass_context
def command(ctx: click.Context) -> None:
    """Show top-level help."""
    parent = ctx.parent
    click.echo(parent.get_help() if parent is not None else ctx.get_help())
