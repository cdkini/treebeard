"""CLI entry point for `tb`.

Subcommands live in `treebeard.commands` and are auto-registered at import time.
To add a new command, drop a module into `treebeard/commands/` that defines a
`click.Command` (or `click.Group`) named `command`.
"""

from __future__ import annotations

import pathlib
from datetime import UTC, datetime

import click

from treebeard import __version__, dependencies, git, ui
from treebeard.cli_help import RichGroup
from treebeard.commands import iter_commands
from treebeard.config import load_sync_warn_threshold, load_vault_path
from treebeard.editor import apply_post_edit
from treebeard.indexer import build_indexes
from treebeard.post_edit import PostEditAbort
from treebeard.timefmt import now_utc


@click.group(
    cls=RichGroup,
    help="tb — a personal-notes CLI.",
    context_settings={"help_option_names": ["-h", "--help"]},
)
@click.version_option(__version__, "-V", "--version", prog_name="tb")
@click.pass_context
def cli(ctx: click.Context) -> None:
    """Root command group."""
    dependencies.check_all()
    ctx.call_on_close(lambda: _on_close(ctx))


def _run_post_edit_hooks(vault: pathlib.Path) -> None:
    """Run `apply_post_edit` on every dirty root-level `.md` in `vault`.

    The porcelain sweep catches both files the subcommand explicitly
    opened *and* files the user side-jumped to (via wikilinks, `gf`,
    `:e other.md`). Per-file `PostEditAbort` (filename collision,
    daily-tag protection) is logged and the loop continues — the user's
    edit stays on disk and the auto-commit captures it.
    """
    now = now_utc()
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
    (e.g. `tb --help`).

    Order matters: the sweep may rename files and bump `updated_at`, and
    those changes need to land in the same commit as the user's edits.
    """
    sub = ctx.invoked_subcommand
    if sub is None:
        return
    try:
        vault = load_vault_path()
        if vault is None or not (vault / ".git").is_dir():
            return
        _run_post_edit_hooks(vault)
        # Auto-index is a convenience, not load-bearing. Isolate its
        # failures so a broken pass can't sink the user's auto-commit.
        try:
            index_stats = build_indexes(vault, now=now_utc())
            for warning in index_stats.warnings:
                ui.warn(warning)
        except Exception as exc:
            ui.warn(f"auto-index failed: {exc}")
        if git.has_changes(vault):
            ts = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
            git.commit_all(vault, f"{sub}: {ts}")

        ahead = git.unsynced_commit_count(vault)
        threshold = load_sync_warn_threshold()
        if ahead is not None and ahead >= threshold:
            ui.warn(f"{ahead} unsynced commits — run [bold]tb sync[/bold] to push to remote.")
    except Exception:
        return


for command in iter_commands():
    cli.add_command(command)


if __name__ == "__main__":
    cli()
