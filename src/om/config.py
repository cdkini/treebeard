"""Shared config helpers for `om`.

The CLI persists a small TOML config at `<config_dir>/config.toml`,
where `<config_dir>` defaults to `~/.om`. Five keys are recognized:
`vault` (absolute path to the user's vault), `editor` (executable to
launch for editing notes; one of `vim`, `nvim`), `previewer` (markdown
previewer used in `om find`'s preview pane; one of `bat`, `glow`,
`cat`), `chat_model` (the Claude model id used by `om chat`), and
`sync_warn_threshold` (number of unsynced commits before the CLI
prompts the user to run `om sync`).
"""

from __future__ import annotations

import os
import pathlib
import tomllib
from collections.abc import Callable
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

DEFAULT_SYNC_WARN_THRESHOLD = 10


@dataclass(frozen=True)
class Config:
    vault: pathlib.Path
    editor: str = DEFAULT_EDITOR
    previewer: str = DEFAULT_PREVIEWER
    chat_model: str = DEFAULT_CHAT_MODEL
    sync_warn_threshold: int = DEFAULT_SYNC_WARN_THRESHOLD

    def to_toml(self) -> str:
        # Inline `# options:` comments document each enum-like field so
        # users hand-editing the file see the alternatives without having
        # to dig through the source.
        lines = [
            f"vault = {_toml_str(str(self.vault))}\n",
            f"editor = {_toml_str(self.editor)}  # options: {', '.join(VALID_EDITORS)}\n",
            f"previewer = {_toml_str(self.previewer)}  # options: {', '.join(VALID_PREVIEWERS)}\n",
            f"chat_model = {_toml_str(self.chat_model)}"
            f"  # options: {', '.join(VALID_CHAT_MODEL_CHOICES)}"
            " (or any pinned model id like claude-sonnet-4-6)\n",
            f"sync_warn_threshold = {self.sync_warn_threshold}\n",
        ]
        return "".join(lines)

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


def _toml_str(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


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
    """Read the config file. Raises `OmError` if no vault is configured,
    the configured vault isn't a valid om vault, or `sync_warn_threshold`
    is present but not a positive integer. Other optional fields fall
    back to their defaults silently."""
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
    return Config(
        vault=vault,
        editor=editor,
        previewer=previewer,
        chat_model=chat_model,
        sync_warn_threshold=_parse_sync_warn_threshold(raw.get("sync_warn_threshold")),
    )


def _parse_sync_warn_threshold(value: object) -> int:
    """Default when missing; raise on present-but-malformed values so
    hand-editors get told they typoed instead of silently getting the
    default. `bool` is excluded explicitly because Python's `True`/`False`
    are `int` subclasses."""
    if value is None:
        return DEFAULT_SYNC_WARN_THRESHOLD
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise OmError(
            f"sync_warn_threshold must be a positive integer (got {value!r})",
            hint="edit config.toml and set e.g. `sync_warn_threshold = 10`",
        )
    return value


def _read_field[T](
    config_dir: str | None,
    key: str,
    parse: Callable[[object], T],
    default: T,
) -> T:
    """Tolerant single-field lookup. Used by the post-command close
    hook: returns `default` if the file can't be read or `parse` raises,
    so a malformed config never crashes a successful command. Strict
    validation lives in `load_config`, which subcommands hit on entry —
    we deliberately don't surface typos on the edit that introduced
    them; the *next* subcommand reads more naturally as the failure
    point."""
    try:
        raw = _read_raw(config_path_for(config_dir))
    except Exception:
        return default
    try:
        return parse(raw.get(key))
    except Exception:
        return default


def _parse_vault_path(value: object) -> pathlib.Path | None:
    if not isinstance(value, str) or not value:
        return None
    return pathlib.Path(value)


def load_vault_path(config_dir: str | None) -> pathlib.Path | None:
    """Best-effort vault lookup. Returns None on any read failure so
    callers can no-op silently."""
    return _read_field(config_dir, "vault", _parse_vault_path, None)


def load_sync_warn_threshold(config_dir: str | None) -> int:
    """Best-effort threshold lookup for the post-command close hook."""
    return _read_field(
        config_dir,
        "sync_warn_threshold",
        _parse_sync_warn_threshold,
        DEFAULT_SYNC_WARN_THRESHOLD,
    )
