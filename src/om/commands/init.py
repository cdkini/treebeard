"""`om init` — interactively scaffold a vault and persist its location."""

from __future__ import annotations

import pathlib
import re
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import click
from rich.panel import Panel
from rich.text import Text

from om import dependencies, git, scaffold, ui
from om.config import (
    CONFIG_FILENAME,
    DEFAULT_CHAT_MODEL,
    DEFAULT_CONFIG_DIR,
    DEFAULT_PREVIEWER,
    VALID_CHAT_MODEL_CHOICES,
    VALID_EDITORS,
    VALID_PREVIEWERS,
    Config,
    config_path_for,
    is_initialized,
    resolve_user_path,
)
from om.ui import OmError, status_console

# Permissive but useful: scheme-based URLs (https/ssh/git/file) or
# scp-like (`user@host:path`). The point is to catch typos like a stray
# space, not to validate that the remote actually resolves.
_REMOTE_URL_RE = re.compile(
    r"^(?:[a-z][a-z0-9+.\-]*://\S+|[\w.\-]+@[\w.\-]+:\S+|/\S+)$",
    re.IGNORECASE,
)


def _ask(label: str, **kwargs: Any) -> str:
    """Prompt with a styled `▸` glyph. Thin wrapper around `click.prompt`
    so all init prompts read consistently."""
    styled = click.style(f"▸ {label}", fg="cyan", bold=True)
    return click.prompt(styled, **kwargs)


def _ask_until(
    label: str,
    validate: Callable[[str], str | None],
    **kwargs: Any,
) -> str:
    """Prompt until `validate(value)` returns `None`. Any other return
    value is treated as a warning message and the prompt is repeated."""
    while True:
        value = str(_ask(label, **kwargs)).strip()
        problem = validate(value)
        if problem is None:
            return value
        ui.warn(problem)


def _ask_choice(label: str, choices: tuple[str, ...], default: str | None) -> str:
    """Choice prompt with our `⚠` warning instead of Click's `Error: ...`.

    Wraps `_ask` rather than Click's built-in `type=Choice` so an invalid
    answer is surfaced via `ui.warn` (consistent with the rest of init)
    and the prompt repeats with the same default still highlighted.
    """
    rendered_default = default if default is not None else ""
    suffix = f" ({', '.join(choices)})"
    while True:
        raw = _ask(
            f"{label}{suffix}",
            default=rendered_default,
            show_default=bool(default),
        )
        value = str(raw).strip()
        if value in choices:
            return value
        ui.warn(f"pick one of: {', '.join(choices)}")


def _section(title: str) -> None:
    status_console.print(f"\n[bold]{title}[/bold]")


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
        raise OmError(f"{config_path_for(config_dir)} already configured; refusing to overwrite")

    status_console.print(
        Panel(
            Text("Set up your om vault — answer a few prompts.", style="dim"),
            title="[bold]om init[/bold]",
            border_style="cyan",
            expand=False,
        )
    )

    _section("Vault")
    vault_path = _prompt_vault_path()

    _section("Tools")
    editor = _prompt_editor()
    previewer = _prompt_previewer()
    chat_model = _prompt_chat_model()

    config = Config(
        vault=vault_path,
        editor=editor,
        previewer=previewer,
        chat_model=chat_model,
    )

    # A valid om vault has both `.om/` and `.git/`. If the user pointed
    # at one, it's already fully scaffolded (typically a clone of a
    # vault from another machine) — leave the directory alone and just
    # record where it lives in `~/.om/config.toml`.
    if (vault_path / ".om").is_dir() and (vault_path / ".git").is_dir():
        config_path = config.save(config_dir)
        status_console.print()
        ui.success(f"Adopted existing vault at {vault_path}")
        ui.success(f"Wrote config to {config_path}")
        return

    vault_path.mkdir(parents=True, exist_ok=True)
    (vault_path / ".om").mkdir(exist_ok=True)
    git.ensure_initialized(vault_path)

    _section("Git")
    _ensure_git_identity(vault_path)
    if not git.has_remote(vault_path):
        _maybe_add_remote(vault_path)

    claude_md_path = vault_path / ".claude" / "CLAUDE.md"
    claude_md_path.parent.mkdir(parents=True, exist_ok=True)
    claude_md_path.write_text(scaffold.compose_claude_md(), encoding="utf-8")

    config_path = config.save(config_dir)

    ts = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    git.commit_all(vault_path, f"init: {ts}")

    status_console.print()
    ui.success(f"Initialized vault at {vault_path}")
    ui.success(f"Scaffolded {claude_md_path}")
    ui.success(f"Wrote config to {config_path}")


def _validate_vault_path(raw: str) -> str | None:
    """Return `None` if `raw` is an acceptable vault target, else a
    warning message. Existence isn't required (the path may be created),
    but the value must look like a path. Bare tokens with no separator
    and no `~` are rejected — they're almost always typos, not the
    intended cwd-relative directory name. Type `./vault` to be explicit."""
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


def _prompt_vault_path() -> pathlib.Path:
    raw = _ask_until("Where should your vault live?", _validate_vault_path, type=str)
    return resolve_user_path(raw)


def _prompt_editor() -> str:
    found = dependencies.first_available(dependencies.EDITORS)
    default = found.name if found is not None else None
    return _ask_choice("Editor", VALID_EDITORS, default)


def _prompt_chat_model() -> str:
    """Pick which Claude family `om chat` will use.

    Stores the family alias (`sonnet`/`opus`) in config.toml rather
    than a pinned model id, so the bundled `claude` CLI resolves it to
    whatever the current generation is — `om chat` rolls forward when
    a newer Sonnet/Opus ships, no config edit needed. Sonnet is the
    default: faster TTFT and lower cost, which matters for an
    interactive chat REPL.
    """
    return _ask_choice("Chat model", VALID_CHAT_MODEL_CHOICES, DEFAULT_CHAT_MODEL)


def _prompt_previewer() -> str:
    """Markdown previewer used in `om find`'s preview pane.

    Default to the first installed option, falling back to `glow` when
    none are present (the runtime fallback in `find._preview_cmd` then
    degrades to `bat`/`cat` if the chosen tool is missing at use time).
    """
    found = dependencies.first_available(dependencies.PREVIEWERS)
    default = found.name if found is not None else DEFAULT_PREVIEWER
    return _ask_choice("Markdown previewer", VALID_PREVIEWERS, default)


def _validate_email(value: str) -> str | None:
    if not value:
        return "email must not be empty"
    if "@" not in value or " " in value:
        return f"`{value}` doesn't look like an email"
    return None


def _validate_name(value: str) -> str | None:
    if not value:
        return "name must not be empty"
    return None


def _validate_remote_url(value: str) -> str | None:
    if not value:
        return None  # blank skips
    if not _REMOTE_URL_RE.match(value):
        return (
            f"`{value}` doesn't look like a git remote — try a URL like "
            "`git@github.com:user/repo.git` or `https://github.com/user/repo.git`"
        )
    return None


def _ensure_git_identity(vault: pathlib.Path) -> None:
    """Make sure user.email and user.name are set in this repo. Defaults
    to whatever git already resolves (typically the global config); the
    user can override at the prompt. Stored in `<vault>/.git/config`."""
    email_default = git.get_config(vault, "user.email")
    email = _ask_until(
        "Email",
        _validate_email,
        default=email_default,
        show_default=email_default is not None,
    )
    git.set_config(vault, "user.email", email)

    name_default = git.get_config(vault, "user.name")
    name = _ask_until(
        "Name",
        _validate_name,
        default=name_default,
        show_default=name_default is not None,
    )
    git.set_config(vault, "user.name", name)


def _ensure_claude_md(vault: pathlib.Path) -> str:
    """Scaffold `<vault>/.claude/CLAUDE.md` for `om chat`'s project memory.

    The Claude Agent SDK auto-loads this file via
    `setting_sources=["project"]`. Writing it under `.claude/` (vs. the
    vault root) keeps it out of `om grep` (ripgrep skips dotdirs) and,
    once `vault.list_recent_notes` prunes dotdirs, out of `om find` too.

    Skips silently when the file already exists — typical for adoption
    of a vault the user has already curated. Returns the status line
    the caller surfaces via `ui.success`.
    """
    target = vault / ".claude" / "CLAUDE.md"
    if target.exists():
        return f"CLAUDE.md already present at {target} — keeping yours"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(scaffold.compose_claude_md(), encoding="utf-8")
    return f"Scaffolded {target}"


def _maybe_add_remote(vault: pathlib.Path) -> None:
    """Optional: add an `origin` remote pointing at a URL the user
    provides. Blank input skips."""
    url = _ask_until(
        "Remote URL (blank to skip)",
        _validate_remote_url,
        default="",
        show_default=False,
    )
    if not url:
        return
    git.add_remote(vault, "origin", url)
