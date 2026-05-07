"""CLI entry point for `om`.

Subcommands live in `om.commands` and are auto-registered at import time.
To add a new command, drop a module into `om/commands/` that defines a
`click.Command` (or `click.Group`) named `command`.
"""

from __future__ import annotations

from datetime import UTC, datetime

import click

from om import __version__, git
from om.commands import iter_commands
from om.config import load_vault_path


@click.group(
    help="om — the omniscience CLI.",
    context_settings={"help_option_names": ["-h", "--help"]},
    invoke_without_command=True,
)
@click.version_option(__version__, "-V", "--version", prog_name="om")
@click.pass_context
def cli(ctx: click.Context) -> None:
    """Root command group. Bare `om` launches the picker."""
    # Subcommands set ctx.obj["config_dir"] so the auto-commit hook
    # targets the same vault the command actually used.
    ctx.ensure_object(dict)
    ctx.call_on_close(lambda: _on_close(ctx))

    if ctx.invoked_subcommand is None:
        # Dispatch to the find picker with the recent-only cap. Setting
        # invoked_subcommand makes `_on_close` run the auto-commit path
        # with subject "find" (instead of bailing out for a no-subcommand
        # call).
        from om.commands import find as find_cmd

        ctx.invoked_subcommand = "find"
        ctx.invoke(find_cmd.command, limit=find_cmd.BARE_LIMIT, config_dir=None)


def _on_close(ctx: click.Context) -> None:
    """Auto-commit any working-tree changes left by the subcommand.
    No-ops when no subcommand ran (e.g. `om --help`)."""
    sub = ctx.invoked_subcommand
    if sub is None:
        return
    try:
        vault = load_vault_path(ctx.obj.get("config_dir"))
        if vault is None or not (vault / ".git").is_dir():
            return
        if not git.has_changes(vault):
            return
        ts = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        git.commit_all(vault, f"{sub}: {ts}")
    except Exception:
        return


for command in iter_commands():
    cli.add_command(command)


if __name__ == "__main__":
    cli()
