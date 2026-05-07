"""`om note [NAME]` — create or open a markdown note in the configured vault."""

from __future__ import annotations

import os
import re
import subprocess
import tempfile
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import click

from om.config import (
    CONFIG_FILENAME,
    DEFAULT_CONFIG_DIR,
    load_vault_path,
)
from om.frontmatter import Frontmatter, split_document

TIMESTAMP_FILENAME_FMT = "%Y-%m-%dT%H-%M-%S"
DRAFTS_DIRNAME = ".om/drafts"

_SLUG_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")


def _now_utc() -> datetime:
    return datetime.now(UTC)


def _slugify(name: str) -> str:
    lowered = name.lower()
    if lowered.endswith(".md"):
        lowered = lowered[:-3]
    slug = _SLUG_NON_ALNUM_RE.sub("-", lowered).strip("-")
    if not slug:
        raise click.ClickException(f"name {name!r} produces an empty slug")
    return slug


def _resolve_editor() -> str:
    raw = os.environ.get("EDITOR", "").strip()
    if not raw:
        return "vi"
    if any(ch.isspace() for ch in raw):
        raise click.ClickException(f"EDITOR must be a single executable; got {raw!r}")
    return raw


def _run_editor(editor: str, path: Path) -> None:
    try:
        subprocess.run([editor, str(path)], check=True)
    except subprocess.CalledProcessError as exc:
        raise click.ClickException(f"editor exited with status {exc.returncode}") from exc


@click.command("note")
@click.argument("name", required=False)
@click.option(
    "--config-dir",
    "config_dir",
    type=click.Path(),
    default=None,
    help=f"Directory holding {CONFIG_FILENAME} (default: {DEFAULT_CONFIG_DIR}).",
)
@click.pass_context
def command(ctx: click.Context, name: str | None, config_dir: str | None) -> None:
    """Create or open a markdown note in the vault and edit it with $EDITOR."""
    ctx.ensure_object(dict)["config_dir"] = config_dir
    vault = load_vault_path(config_dir)
    if vault is None:
        raise click.ClickException("no vault configured; run `om init` first")
    if not vault.is_dir():
        raise click.ClickException(f"configured vault {vault} does not exist")

    editor = _resolve_editor()
    now = _now_utc()

    if name is None:
        _create_unnamed(vault, now, editor)
        return

    slug = _slugify(name)
    stripped = name[:-3] if name.endswith(".md") else name
    create_or_open_named(vault, slug, stripped, now, editor)


def create_or_open_named(
    vault: Path,
    slug: str,
    title: str,
    now: datetime,
    editor: str,
    *,
    tags: list[str] | None = None,
) -> None:
    """Create `vault/{slug}.md` with frontmatter and open `$EDITOR`,
    or reopen it if it already exists."""
    path = vault / f"{slug}.md"
    if not path.exists():
        _create_named(path, title, now, editor, tags=tags or [])
        return
    _reopen(path, editor)


def _create_named(
    path: Path,
    title: str,
    now: datetime,
    editor: str,
    *,
    tags: list[str] | None = None,
) -> None:
    fm = Frontmatter.new(title, now)
    if tags:
        fm.tags = list(tags)
    initial = fm.serialize() + "\n"
    path.write_text(initial, encoding="utf-8")
    try:
        _run_editor(editor, path)
    except click.ClickException:
        path.unlink(missing_ok=True)
        raise

    contents = path.read_text(encoding="utf-8")
    if contents == initial:
        path.unlink()
        click.echo(f"discarded empty note: {path}")
        return

    _rewrite_with(path, contents, mutate=lambda fm: None)
    click.echo(str(path))


def _create_unnamed(vault: Path, now: datetime, editor: str) -> None:
    drafts_dir = vault / DRAFTS_DIRNAME
    drafts_dir.mkdir(parents=True, exist_ok=True)

    initial = Frontmatter.new("", now).serialize() + "\n"
    fd, raw_path = tempfile.mkstemp(suffix=".md", dir=str(drafts_dir))
    draft = Path(raw_path)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(initial)

    try:
        _run_editor(editor, draft)
    except click.ClickException:
        draft.unlink(missing_ok=True)
        raise

    contents = draft.read_text(encoding="utf-8")
    if contents == initial:
        draft.unlink()
        click.echo("discarded empty note")
        return

    parsed = split_document(contents)
    edited_title = parsed[0].title.strip() if parsed is not None else ""
    if edited_title:
        title = edited_title
        slug = _slugify(edited_title)
    else:
        ts = now.strftime(TIMESTAMP_FILENAME_FMT)
        title = f"Scratch {ts}"
        slug = f"scratch-{ts.lower()}"

    final_path = vault / f"{slug}.md"
    if final_path.exists():
        raise click.ClickException(f"{final_path} already exists; draft kept at {draft}")

    def fill_title(fm: Frontmatter) -> None:
        if not fm.title.strip():
            fm.title = title

    _rewrite_with(draft, contents, mutate=fill_title)
    draft.rename(final_path)
    click.echo(str(final_path))


def _reopen(path: Path, editor: str) -> None:
    mtime_before = path.stat().st_mtime_ns
    _run_editor(editor, path)
    mtime_after = path.stat().st_mtime_ns
    if mtime_after != mtime_before:
        contents = path.read_text(encoding="utf-8")
        bump_ts = _now_utc()

        def bump(fm: Frontmatter) -> None:
            fm.updated_at = bump_ts

        _rewrite_with(path, contents, mutate=bump)
    click.echo(str(path))


def _rewrite_with(path: Path, contents: str, mutate: Callable[[Frontmatter], None]) -> None:
    """Re-serialize the frontmatter after applying `mutate(fm)`.

    No-op if the file lacks a parseable frontmatter block — we don't
    inject one we'd be guessing at.
    """
    parsed = split_document(contents)
    if parsed is None:
        return
    fm, body = parsed
    mutate(fm)
    path.write_text(fm.serialize() + body, encoding="utf-8")
