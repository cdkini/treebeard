"""CLI entry point for `om`.

Subcommands live in `om.commands` and are auto-registered at import time.
To add a new command, drop a module into `om/commands/` that defines a
`click.Command` (or `click.Group`) named `command`.
"""

from __future__ import annotations

import io
import pathlib
from datetime import UTC, datetime

import click
from rich.console import Console
from rich.table import Table
from rich.text import Text

from om import __version__, dependencies, editor, git, ui
from om.commands import iter_commands
from om.config import load_sync_warn_threshold, load_vault_path
from om.editor import apply_post_edit
from om.post_edit import PostEditAbort


class RichGroup(click.Group):
    """`click.Group` that renders help as a Rich table.

    The header (usage + description), options, and epilog stay on Click's
    formatter so flag handling stays canonical. Only the "Commands"
    section is replaced with a Rich `Table` keyed by command name.
    """

    def format_commands(self, ctx: click.Context, formatter: click.HelpFormatter) -> None:
        commands = [(name, self.get_command(ctx, name)) for name in self.list_commands(ctx)]
        commands = [(n, c) for n, c in commands if c is not None and not c.hidden]
        if not commands:
            return

        table = Table(
            show_header=False,
            box=None,
            padding=(0, 2),
            pad_edge=False,
        )
        table.add_column(style="bold cyan", no_wrap=True)
        table.add_column(style="white")
        for name, cmd in commands:
            help_text = cmd.get_short_help_str(limit=120) if cmd is not None else ""
            table.add_row(name, Text(help_text))

        # Render the Rich table to a string and feed it into Click's
        # formatter buffer so `--help` output remains a single contiguous
        # chunk (and `om help` echoing `parent.get_help()` works).
        # `force_terminal=True` makes Rich emit ANSI escapes into the
        # StringIO buffer; `click.echo` then strips them when the final
        # sink isn't an interactive terminal, so pipes stay clean.
        buf = io.StringIO()
        Console(file=buf, force_terminal=True, width=formatter.width or 100).print(
            "\n[bold]Commands[/bold]"
        )
        Console(file=buf, force_terminal=True, width=formatter.width or 100).print(table)
        formatter.write(buf.getvalue())


@click.group(
    cls=RichGroup,
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
    dependencies.check_all()
    ctx.call_on_close(lambda: _on_close(ctx))

    if ctx.invoked_subcommand is None:
        # Dispatch to the find picker with the recent-only cap. Setting
        # invoked_subcommand makes `_on_close` run the auto-commit path
        # with subject "find" (instead of bailing out for a no-subcommand
        # call).
        from om.commands import find as find_cmd

        ctx.invoked_subcommand = "find"
        ctx.invoke(find_cmd.command, limit=find_cmd.BARE_LIMIT, config_dir=None)


def _run_post_edit_hooks(vault: pathlib.Path) -> None:
    """Run `apply_post_edit` on every dirty root-level `.md` in `vault`.

    The porcelain sweep catches both files the subcommand explicitly
    opened *and* files the user side-jumped to (via wikilinks, `gf`,
    `:e other.md`). Per-file `PostEditAbort` (filename collision,
    daily-tag protection) is logged and the loop continues — the user's
    edit stays on disk and the auto-commit captures it.
    """
    now = editor._now_utc()
    for path in git.changed_root_md_paths(vault):
        try:
            final = apply_post_edit(path, now=now)
        except PostEditAbort as exc:
            ui.warn(f"could not reconcile {path.name}: {exc}")
            continue
        ui.path(str(final))


def _on_close(ctx: click.Context) -> None:
    """Run the post-edit sweep, auto-commit any working-tree changes left
    by the subcommand, then warn if local commits have piled up past the
    configured `sync_warn_threshold`. No-ops when no subcommand ran
    (e.g. `om --help`).

    Order matters: the sweep may rename files and bump `updated_at`, and
    those changes need to land in the same commit as the user's edits.
    """
    sub = ctx.invoked_subcommand
    if sub is None:
        return
    try:
        config_dir = ctx.obj.get("config_dir")
        vault = load_vault_path(config_dir)
        if vault is None or not (vault / ".git").is_dir():
            return
        _run_post_edit_hooks(vault)
        if git.has_changes(vault):
            ts = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
            git.commit_all(vault, f"{sub}: {ts}")

        ahead = git.unsynced_commit_count(vault)
        threshold = load_sync_warn_threshold(config_dir)
        if ahead is not None and ahead >= threshold:
            ui.warn(f"{ahead} unsynced commits — run [bold]om sync[/bold] to push to remote.")
    except Exception:
        return


for command in iter_commands():
    cli.add_command(command)


if __name__ == "__main__":
    cli()
