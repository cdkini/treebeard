"""`om init <path>` — scaffold a vault and persist its location."""

from __future__ import annotations

import os
import tomllib
from pathlib import Path
from typing import Any

import click

DEFAULT_CONFIG_DIR = "~/.om"
CONFIG_FILENAME = "config.toml"
VAULT_MARKER = ".om-vault"


def _resolve_user_path(raw: str) -> Path:
    return Path(os.path.expandvars(raw)).expanduser().resolve()


def _read_config(config_path: Path) -> dict[str, Any]:
    if not config_path.exists():
        return {}
    with config_path.open("rb") as fh:
        return dict(tomllib.load(fh))


def _write_config(config_path: Path, data: dict[str, Any]) -> None:
    lines: list[str] = []
    for key, value in data.items():
        if not isinstance(value, str):
            raise click.ClickException(
                f"unsupported config value for {key!r}: only strings are supported"
            )
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        lines.append(f'{key} = "{escaped}"\n')
    config_path.write_text("".join(lines), encoding="utf-8")


@click.command("init")
@click.argument("path", type=click.Path())
@click.option(
    "--config-dir",
    "config_dir",
    type=click.Path(),
    default=None,
    help=f"Directory holding {CONFIG_FILENAME} (default: {DEFAULT_CONFIG_DIR}).",
)
def command(path: str, config_dir: str | None) -> None:
    """Scaffold a new om vault at PATH and record it in the config file."""
    vault_path = _resolve_user_path(path)

    if vault_path.exists():
        raise click.ClickException(f"{vault_path} already exists")
    if not vault_path.parent.exists():
        raise click.ClickException(f"parent directory {vault_path.parent} does not exist")

    raw_config_dir = config_dir if config_dir is not None else DEFAULT_CONFIG_DIR
    config_dir_path = _resolve_user_path(raw_config_dir)
    config_path = config_dir_path / CONFIG_FILENAME

    existing = _read_config(config_path)
    if "vault" in existing:
        raise click.ClickException(
            f"{config_path} already has a vault configured ({existing['vault']}); "
            "refusing to overwrite"
        )

    vault_path.mkdir()
    (vault_path / VAULT_MARKER).touch()

    config_dir_path.mkdir(parents=True, exist_ok=True)
    existing["vault"] = str(vault_path)
    _write_config(config_path, existing)

    click.echo(f"Initialized vault at {vault_path}")
    click.echo(f"Wrote config to {config_path}")
