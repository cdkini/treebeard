"""Shared config helpers for `om`.

The CLI persists a small TOML config at `<config_dir>/config.toml`,
where `<config_dir>` defaults to `~/.om`. Four keys are recognized:
`vault` (absolute path to the user's vault), `editor` (executable
to launch for editing notes; `om init` prompts for one of `vim`,
`nvim`), `previewer` (markdown previewer used in `om find`'s
preview pane; one of `bat`, `glow`, `cat`), and `chat_model` (the
Claude model id used by `om chat`).
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

# Aliases the bundled `claude` CLI itself resolves to the current
# generation of each family — pinning the alias (rather than a specific
# model id like `claude-sonnet-4-6`) means `om chat` rolls forward
# automatically when Anthropic ships a newer Sonnet/Opus. Users can
# still hand-edit `chat_model` to a pinned id if they want stability.
VALID_CHAT_MODEL_CHOICES = ("sonnet", "opus")
DEFAULT_CHAT_MODEL = "sonnet"


@dataclass(frozen=True)
class Config:
    vault: pathlib.Path
    editor: str
    previewer: str = DEFAULT_PREVIEWER
    chat_model: str = DEFAULT_CHAT_MODEL

    def to_toml(self) -> str:
        return _toml_lines(
            {
                "vault": str(self.vault),
                "editor": self.editor,
                "previewer": self.previewer,
                "chat_model": self.chat_model,
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
    chat_model_raw = raw.get("chat_model")
    # Pass-through: any string the `claude` CLI accepts (`sonnet`,
    # `opus`, or a pinned id like `claude-sonnet-4-6`) is fine here.
    if isinstance(chat_model_raw, str) and chat_model_raw:
        chat_model = chat_model_raw
    else:
        chat_model = DEFAULT_CHAT_MODEL
    return Config(vault=vault, editor=editor, previewer=previewer, chat_model=chat_model)


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
