"""`om init` — interactively scaffold a vault and persist its location."""

from __future__ import annotations

import shutil
from pathlib import Path

import click

from om.config import (
    CONFIG_FILENAME,
    DEFAULT_CONFIG_DIR,
    VALID_EDITORS,
    Config,
    config_path_for,
    is_initialized,
    resolve_user_path,
)


@click.command("init")
@click.option(
    "--config-dir",
    "config_dir",
    type=click.Path(),
    default=None,
    help=f"Directory holding {CONFIG_FILENAME} (default: {DEFAULT_CONFIG_DIR}).",
)
@click.pass_context
def command(ctx: click.Context, config_dir: str | None) -> None:
    """Scaffold a new om vault and record its location and editor choice."""
    ctx.ensure_object(dict)["config_dir"] = config_dir

    if is_initialized(config_dir):
        raise click.ClickException(
            f"{config_path_for(config_dir)} already configured; refusing to overwrite"
        )

    vault_path = _prompt_vault_path()
    editor = _prompt_editor()

    vault_path.mkdir()
    (vault_path / ".om").mkdir()

    config = Config(vault=vault_path, editor=editor)
    config_path = config.save(config_dir)

    click.echo(f"Initialized vault at {vault_path}")
    click.echo(f"Wrote config to {config_path}")


def _prompt_vault_path() -> Path:
    while True:
        raw = click.prompt("Vault path", type=str).strip()
        if not raw:
            click.echo("path must not be empty")
            continue
        candidate = resolve_user_path(raw)
        if candidate.exists():
            click.echo(f"{candidate} already exists")
            continue
        if not candidate.parent.exists():
            click.echo(f"parent directory {candidate.parent} does not exist")
            continue
        return candidate


def _prompt_editor() -> str:
    default = next((name for name in VALID_EDITORS if shutil.which(name)), None)
    return click.prompt(
        "Editor",
        type=click.Choice(VALID_EDITORS),
        default=default,
        show_choices=True,
    )
