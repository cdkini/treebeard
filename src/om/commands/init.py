"""`om init` — interactively scaffold a vault and persist its location."""

from __future__ import annotations

import pathlib
import shutil
from datetime import UTC, datetime

import click

from om import git
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
    """Scaffold a new om vault or link to an existing one."""
    ctx.ensure_object(dict)["config_dir"] = config_dir

    if is_initialized(config_dir):
        raise click.ClickException(
            f"{config_path_for(config_dir)} already configured; refusing to overwrite"
        )

    vault_path = _prompt_vault_path()
    editor = _prompt_editor()

    adopted = (vault_path / ".om").is_dir() and (vault_path / ".git").is_dir()

    vault_path.mkdir(parents=True, exist_ok=True)
    (vault_path / ".om").mkdir(exist_ok=True)
    git.ensure_initialized(vault_path)

    _ensure_git_identity(vault_path)
    if not git.has_remote(vault_path):
        _maybe_add_remote(vault_path)

    config = Config(vault=vault_path, editor=editor)
    config_path = config.save(config_dir)

    # Guarantee the repo has a HEAD so downstream commands (sync, the
    # auto-commit hook, etc.) don't trip on a commitless repo. Skip when
    # adopting a vault that already has commits — don't pollute history.
    if not git.has_head(vault_path):
        ts = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        message = f"init: {ts}"
        if git.has_changes(vault_path):
            git.commit_all(vault_path, message)
        else:
            git.commit_all_allow_empty(vault_path, message)

    if adopted:
        click.echo(f"Adopted existing vault at {vault_path}")
    else:
        click.echo(f"Initialized vault at {vault_path}")
    click.echo(f"Wrote config to {config_path}")


def _prompt_vault_path() -> pathlib.Path:
    while True:
        raw = click.prompt("Vault path", type=str).strip()
        if not raw:
            click.echo("path must not be empty")
            continue
        candidate = resolve_user_path(raw)
        if candidate.exists() and not candidate.is_dir():
            click.echo(f"{candidate} is not a directory")
            continue
        if candidate.is_dir():
            has_om = (candidate / ".om").is_dir()
            has_git = (candidate / ".git").is_dir()
            if has_om and not has_git:
                click.echo(
                    f"{candidate} has .om/ but no .git/ "
                    "(corrupted vault — restore from backup or remove .om/ first)"
                )
                continue
            if not has_om and any(candidate.iterdir()):
                click.echo(f"{candidate} is not empty and is not an om vault; refusing to use it")
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


def _ensure_git_identity(vault: pathlib.Path) -> None:
    """Make sure user.email and user.name are set in this repo. Defaults
    to whatever git already resolves (typically the global config); the
    user can override at the prompt. Stored in `<vault>/.git/config`."""
    for key, label in (("user.email", "Git email"), ("user.name", "Git name")):
        existing = git.get_config(vault, key)
        value = click.prompt(label, default=existing, show_default=existing is not None).strip()
        if not value:
            raise click.ClickException(f"{key} is required")
        git.set_config(vault, key, value)


def _maybe_add_remote(vault: pathlib.Path) -> None:
    """Optional: add an `origin` remote pointing at a URL the user
    provides. Blank input skips."""
    url = click.prompt("Git remote URL (blank to skip)", default="", show_default=False).strip()
    if not url:
        return
    git.add_remote(vault, "origin", url)
