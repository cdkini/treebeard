"""`om init <path>` — scaffold a vault at `<path>` and persist its location.

Non-interactive: the path is the only required input. Editor, previewer,
chat model, and sync-warn threshold all fall back to sane defaults; the
user can edit `~/.om/config.toml` later (via `om config`) to change
them. Git identity is inherited from the user's global git config; if
the user wants a remote, they add one with `git remote add origin <url>`
inside the vault.
"""

from __future__ import annotations

from datetime import UTC, datetime

import click

from om import dependencies, git, scaffold, ui
from om.config import (
    DEFAULT_CHAT_MODEL,
    DEFAULT_EDITOR,
    DEFAULT_PREVIEWER,
    Config,
    config_path_for,
    is_initialized,
    resolve_user_path,
)
from om.ui import OmError


@click.command("init")
@click.argument("path", type=click.Path())
def command(path: str) -> None:
    """Scaffold a new om vault at PATH (or adopt one already there)."""
    if is_initialized():
        raise OmError(f"{config_path_for()} already configured; refusing to overwrite")

    problem = _validate_vault_path(path)
    if problem is not None:
        raise OmError(problem)
    vault_path = resolve_user_path(path)

    config = Config(
        vault=vault_path,
        editor=_default_editor(),
        previewer=_default_previewer(),
        chat_model=DEFAULT_CHAT_MODEL,
    )

    # A valid om vault has both `.om/` and `.git/`. If the user pointed
    # at one, it's already fully scaffolded (typically a clone of a
    # vault from another machine) — leave the directory alone and just
    # record where it lives in `~/.om/config.toml`.
    if (vault_path / ".om").is_dir() and (vault_path / ".git").is_dir():
        config_path = config.save()
        ui.success(f"Adopted existing vault at {vault_path}")
        ui.success(f"Wrote config to {config_path}")
        return

    vault_path.mkdir(parents=True, exist_ok=True)
    (vault_path / ".om").mkdir(exist_ok=True)
    git.ensure_initialized(vault_path)

    claude_md_path = vault_path / ".claude" / "CLAUDE.md"
    claude_md_path.parent.mkdir(parents=True, exist_ok=True)
    claude_md_path.write_text(scaffold.compose_claude_md(), encoding="utf-8")

    config_path = config.save()

    ts = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    git.commit_all(vault_path, f"init: {ts}")

    ui.success(f"Initialized vault at {vault_path}")
    ui.success(f"Scaffolded {claude_md_path}")
    ui.success(f"Wrote config to {config_path}")


def _validate_vault_path(raw: str) -> str | None:
    """Return `None` if `raw` is an acceptable vault target, else a
    user-facing error message. Existence isn't required (the path may be
    created), but the value must look like a path. Bare tokens with no
    separator and no `~` are rejected — they're almost always typos, not
    the intended cwd-relative directory name. Type `./vault` to be
    explicit."""
    if not raw:
        return "path must not be empty"
    if "/" not in raw and not raw.startswith("~"):
        return f"`{raw}` doesn't look like a path — try an absolute path or one with a `/`"
    candidate = resolve_user_path(raw)
    if candidate.exists() and not candidate.is_dir():
        return f"{candidate} is not a directory"
    if candidate.is_dir():
        has_om = (candidate / ".om").is_dir()
        has_git = (candidate / ".git").is_dir()
        if has_om and not has_git:
            return (
                f"{candidate} has .om/ but no .git/ "
                "(corrupted vault — restore from backup or remove .om/ first)"
            )
        if not has_om and any(candidate.iterdir()):
            return f"{candidate} is not empty and is not an om vault; refusing to use it"
    return None


def _default_editor() -> str:
    found = dependencies.first_available(dependencies.EDITORS)
    return found.name if found is not None else DEFAULT_EDITOR


def _default_previewer() -> str:
    found = dependencies.first_available(dependencies.PREVIEWERS)
    return found.name if found is not None else DEFAULT_PREVIEWER
