"""`om sync` — pull from and push to the configured git remote."""

from __future__ import annotations

import subprocess

import click

from om import git, ui
from om.config import load_config
from om.ui import OmError


@click.command("sync")
def command() -> None:
    """Pull from and push to the vault's configured git remote."""
    cfg = load_config()

    if not git.has_remote(cfg.vault):
        raise OmError(
            "no git remote configured",
            hint="add one with `git remote add origin <url>`",
        )

    try:
        git.pull_rebase_push(cfg.vault)
    except subprocess.CalledProcessError as exc:
        raise OmError(f"sync failed: {exc}") from exc

    ui.success("Synced.")
