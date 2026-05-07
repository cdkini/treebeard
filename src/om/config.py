"""Shared config helpers for `om`.

The CLI persists a small TOML config at `<config_dir>/config.toml`,
where `<config_dir>` defaults to `~/.om`. Three keys are recognized:
`vault` (absolute path to the user's vault), `editor` (executable
to launch for editing notes; `om init` prompts for one of `vim`,
`nvim`), and `previewer` (markdown previewer used in `om find`'s
preview pane; one of `bat`, `glow`, `cat`).
"""

from __future__ import annotations

import os
import pathlib
import tomllib
from dataclasses import dataclass

from om.ui import OmError

DEFAULT_CONFIG_DIR = "~/.om"
CONFIG_FILENAME = "config.toml"
VALID_EDITORS = ("nvim", "vim")
DEFAULT_EDITOR = "vim"
VALID_PREVIEWERS = ("bat", "glow", "cat")
DEFAULT_PREVIEWER = "bat"


@dataclass(frozen=True)
class Config:
    vault: pathlib.Path
    editor: str
    previewer: str = DEFAULT_PREVIEWER

    def to_toml(self) -> str:
        return _toml_lines(
            {
                "vault": str(self.vault),
                "editor": self.editor,
                "previewer": self.previewer,
            }
        )

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


def is_valid_vault(vault: pathlib.Path) -> tuple[bool, str | None]:
    """An om vault is a directory containing both `.om/` and `.git/`.
    Returns `(ok, reason)` — `reason` is a user-facing string when not ok."""
    if not vault.is_dir():
        return False, f"vault {vault} does not exist"
    if not (vault / ".om").is_dir():
        return False, f"{vault} is missing .om/ (not an om vault)"
    if not (vault / ".git").is_dir():
        return False, f"{vault} is missing .git/ (run `git init` or restore from a backup)"
    return True, None


def load_config(config_dir: str | None) -> Config:
    """Read the config file. Raises `OmError` if no vault is configured
    or the configured vault isn't a valid om vault. Defaults the editor
    to `vim` and the previewer to `glow` when the fields are absent."""
    raw = _read_raw(config_path_for(config_dir))
    vault_raw = raw.get("vault")
    if not isinstance(vault_raw, str) or not vault_raw:
        raise OmError("no vault configured", hint="run `om init` first")
    vault = pathlib.Path(vault_raw)
    ok, reason = is_valid_vault(vault)
    if not ok:
        raise OmError(reason or f"invalid vault {vault}")
    editor = raw.get("editor")
    if not isinstance(editor, str) or not editor:
        editor = DEFAULT_EDITOR
    previewer = raw.get("previewer")
    if not isinstance(previewer, str) or previewer not in VALID_PREVIEWERS:
        previewer = DEFAULT_PREVIEWER
    return Config(vault=vault, editor=editor, previewer=previewer)


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
