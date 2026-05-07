"""Shared config helpers for `om`.

The CLI persists a small TOML config at `<config_dir>/config.toml`,
where `<config_dir>` defaults to `~/.om`. Two keys are recognized:
`vault` (absolute path to the user's vault) and `editor` (executable
to launch for editing notes; `om init` prompts for one of `vim`,
`nvim`).
"""

from __future__ import annotations

import os
import pathlib
import tomllib
from dataclasses import dataclass

import click

DEFAULT_CONFIG_DIR = "~/.om"
CONFIG_FILENAME = "config.toml"
VALID_EDITORS = ("nvim", "vim")
DEFAULT_EDITOR = "vim"


@dataclass(frozen=True)
class Config:
    vault: pathlib.Path
    editor: str

    def to_toml(self) -> str:
        return _toml_lines({"vault": str(self.vault), "editor": self.editor})

    def save(self, config_dir: str | None) -> pathlib.Path:
        """Write `self` to `<config_dir>/config.toml`, creating the
        directory if needed. Returns the file path."""
        path = config_path_for(config_dir)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.to_toml(), encoding="utf-8")
        return path


def resolve_user_path(raw: str) -> pathlib.Path:
    return pathlib.Path(os.path.expandvars(raw)).expanduser().resolve()


def config_path_for(config_dir: str | None) -> pathlib.Path:
    raw = config_dir if config_dir is not None else DEFAULT_CONFIG_DIR
    return resolve_user_path(raw) / CONFIG_FILENAME


def _read_raw(config_path: pathlib.Path) -> dict[str, object]:
    if not config_path.exists():
        return {}
    with config_path.open("rb") as fh:
        return dict(tomllib.load(fh))


def _toml_lines(data: dict[str, str]) -> str:
    out: list[str] = []
    for key, value in data.items():
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        out.append(f'{key} = "{escaped}"\n')
    return "".join(out)


def is_initialized(config_dir: str | None) -> bool:
    """True if a config file already records a vault or editor.
    `om init` uses this to refuse to overwrite an existing setup."""
    raw = _read_raw(config_path_for(config_dir))
    return "vault" in raw or "editor" in raw


def load_config(config_dir: str | None) -> Config:
    """Read the config file. Raises `click.ClickException` if no vault
    is configured or the configured vault is missing. Defaults the
    editor to `vim` when the field is absent."""
    raw = _read_raw(config_path_for(config_dir))
    vault_raw = raw.get("vault")
    if not isinstance(vault_raw, str) or not vault_raw:
        raise click.ClickException("no vault configured; run `om init` first")
    vault = pathlib.Path(vault_raw)
    if not vault.is_dir():
        raise click.ClickException(f"configured vault {vault} does not exist")
    editor = raw.get("editor")
    if not isinstance(editor, str) or not editor:
        editor = DEFAULT_EDITOR
    return Config(vault=vault, editor=editor)


def load_vault_path(config_dir: str | None) -> pathlib.Path | None:
    """Best-effort vault lookup for callers that need only the path
    (e.g. usage logging). Returns None on any read failure so callers
    can no-op silently."""
    try:
        raw = _read_raw(config_path_for(config_dir))
    except Exception:
        return None
    vault = raw.get("vault")
    if not isinstance(vault, str) or not vault:
        return None
    return pathlib.Path(vault)
