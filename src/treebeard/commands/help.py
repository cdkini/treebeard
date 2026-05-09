"""`tb help` — canonical help surface.

Mirrors `tb --help` (and bare `tb`).
"""

from __future__ import annotations

import click


@click.command("help")
@click.pass_context
def command(ctx: click.Context) -> None:
    """Show top-level help."""
    parent = ctx.parent
    click.echo(parent.get_help() if parent is not None else ctx.get_help())
