"""`om init <path>` — scaffold a vault and persist its location."""

from __future__ import annotations

import click

from om.config import (
    CONFIG_FILENAME,
    DEFAULT_CONFIG_DIR,
    config_path_for,
    read_config,
    resolve_user_path,
    write_config,
)


@click.command("init")
@click.argument("path", type=click.Path())
@click.option(
    "--config-dir",
    "config_dir",
    type=click.Path(),
    default=None,
    help=f"Directory holding {CONFIG_FILENAME} (default: {DEFAULT_CONFIG_DIR}).",
)
@click.pass_context
def command(ctx: click.Context, path: str, config_dir: str | None) -> None:
    """Scaffold a new om vault at PATH and record it in the config file."""
    ctx.ensure_object(dict)["config_dir"] = config_dir
    vault_path = resolve_user_path(path)

    if vault_path.exists():
        raise click.ClickException(f"{vault_path} already exists")
    if not vault_path.parent.exists():
        raise click.ClickException(f"parent directory {vault_path.parent} does not exist")

    config_path = config_path_for(config_dir)
    config_dir_path = config_path.parent

    existing = read_config(config_path)
    if "vault" in existing:
        raise click.ClickException(
            f"{config_path} already has a vault configured ({existing['vault']}); "
            "refusing to overwrite"
        )

    vault_path.mkdir()
    (vault_path / ".om").mkdir()

    config_dir_path.mkdir(parents=True, exist_ok=True)
    existing["vault"] = str(vault_path)
    write_config(config_path, existing)

    click.echo(f"Initialized vault at {vault_path}")
    click.echo(f"Wrote config to {config_path}")
