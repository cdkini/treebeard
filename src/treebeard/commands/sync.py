"""`tb sync` — pull from and push to the configured git remote."""

from __future__ import annotations

import subprocess

import click

from treebeard import git, ui
from treebeard.config import load_config
from treebeard.ui import TreebeardError


@click.command("sync")
def command() -> None:
    """Synchronize with the vault's configured git remote."""
    cfg = load_config()

    if not git.has_remote(cfg.vault):
        raise TreebeardError(
            "no git remote configured",
            hint="add one with `git remote add origin <url>`",
        )

    try:
        git.pull_rebase_push(cfg.vault)
    except subprocess.CalledProcessError as exc:
        raise TreebeardError(f"sync failed: {exc}") from exc

    ui.success("Synced.")
