"""`om sync` — pull from and push to the configured git remote."""

from __future__ import annotations

import subprocess

import click

from om import git, ui
from om.config import (
    CONFIG_FILENAME,
    DEFAULT_CONFIG_DIR,
    load_config,
)
from om.ui import OmError


@click.command("sync")
@click.option(
    "--config-dir",
    "config_dir",
    type=click.Path(),
    default=None,
    help=f"Directory holding {CONFIG_FILENAME} (default: {DEFAULT_CONFIG_DIR}).",
)
@click.pass_context
def command(ctx: click.Context, config_dir: str | None) -> None:
    """Pull from and push to the vault's configured git remote."""
    ctx.ensure_object(dict)["config_dir"] = config_dir
    cfg = load_config(config_dir)

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
