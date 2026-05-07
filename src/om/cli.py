"""CLI entry point for `om`.

Subcommands live in `om.commands` and are auto-registered at import time.
To add a new command, drop a module into `om/commands/` that defines a
`click.Command` (or `click.Group`) named `command`.
"""

from __future__ import annotations

from datetime import UTC, datetime

import click

from om import __version__, git, usage_log
from om.commands import iter_commands
from om.config import load_vault_path


@click.group(
    help="om — the omniscience CLI.",
    context_settings={"help_option_names": ["-h", "--help"]},
)
@click.version_option(__version__, "-V", "--version", prog_name="om")
@click.pass_context
def cli(ctx: click.Context) -> None:
    """Root command group."""
    # Subcommands set ctx.obj["config_dir"] so logging targets the same
    # vault the command actually used (not just the default location).
    ctx.ensure_object(dict)
    # One callback so the order is explicit. `call_on_close` runs LIFO,
    # which made a two-callback setup easy to get backwards — see
    # https://click.palletsprojects.com/en/stable/api/#click.Context.call_on_close
    ctx.call_on_close(lambda: _on_close(ctx))


def _on_close(ctx: click.Context) -> None:
    """End-of-command housekeeping: append the usage log line, then
    auto-commit any working-tree changes (including that line). No-ops
    when no subcommand ran (e.g. `om`, `om --help`)."""
    sub = ctx.invoked_subcommand
    if sub is None:
        return
    usage_log.log_invocation(ctx.obj.get("config_dir"), [sub])
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
