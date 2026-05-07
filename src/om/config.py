"""Shared config-file helpers for `om`.

The CLI persists a small TOML config at `<config_dir>/config.toml`,
where `<config_dir>` defaults to `~/.om`. Today the only key is `vault`,
the absolute path to the user's vault.
"""

from __future__ import annotations

import os
import tomllib
from pathlib import Path
from typing import Any

import click

DEFAULT_CONFIG_DIR = "~/.om"
CONFIG_FILENAME = "config.toml"


def resolve_user_path(raw: str) -> Path:
    return Path(os.path.expandvars(raw)).expanduser().resolve()


def config_path_for(config_dir: str | None) -> Path:
    raw = config_dir if config_dir is not None else DEFAULT_CONFIG_DIR
    return resolve_user_path(raw) / CONFIG_FILENAME


def read_config(config_path: Path) -> dict[str, Any]:
    if not config_path.exists():
        return {}
    with config_path.open("rb") as fh:
        return dict(tomllib.load(fh))


def write_config(config_path: Path, data: dict[str, Any]) -> None:
    lines: list[str] = []
    for key, value in data.items():
        if not isinstance(value, str):
            raise click.ClickException(
                f"unsupported config value for {key!r}: only strings are supported"
            )
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        lines.append(f'{key} = "{escaped}"\n')
    config_path.write_text("".join(lines), encoding="utf-8")


def load_vault_path(config_dir: str | None) -> Path | None:
    """Return the configured vault path, or None if unset/unreadable."""
    try:
        cfg = read_config(config_path_for(config_dir))
    except Exception:
        return None
    vault = cfg.get("vault")
    if not isinstance(vault, str) or not vault:
        return None
    return Path(vault)
