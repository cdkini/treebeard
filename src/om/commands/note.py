"""`om note [NAME]` — create or open a markdown note in the configured vault."""

from __future__ import annotations

import os
import re
import tempfile
from datetime import UTC, datetime
from pathlib import Path

import click

from om.config import (
    CONFIG_FILENAME,
    DEFAULT_CONFIG_DIR,
    load_config,
)
from om.editor import edit_with_initial, reopen, rewrite_with, run_editor
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
    """Create or open a markdown note in the vault and edit it with the configured editor."""
    ctx.ensure_object(dict)["config_dir"] = config_dir
    cfg = load_config(config_dir)

    now = _now_utc()

    if name is None:
        _create_unnamed(cfg.vault, now, cfg.editor)
        return

    slug = _slugify(name)
    stripped = name[:-3] if name.endswith(".md") else name
    create_or_open_named(cfg.vault, slug, stripped, now, cfg.editor)


def create_or_open_named(
    vault: Path,
    slug: str,
    title: str,
    now: datetime,
    editor: str,
    *,
    tags: list[str] | None = None,
) -> None:
    """Create `vault/{slug}.md` with frontmatter and open the editor,
    or reopen it if it already exists."""
    path = vault / f"{slug}.md"
    if not path.exists():
        fm = Frontmatter.new(title, now)
        if tags:
            fm.tags = list(tags)
        edit_with_initial(path, fm.serialize() + "\n\n", editor)
        return
    reopen(path, editor)


def _create_unnamed(vault: Path, now: datetime, editor: str) -> None:
    drafts_dir = vault / DRAFTS_DIRNAME
    drafts_dir.mkdir(parents=True, exist_ok=True)

    initial = Frontmatter.new("", now).serialize() + "\n\n"
    fd, raw_path = tempfile.mkstemp(suffix=".md", dir=str(drafts_dir))
    draft = Path(raw_path)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(initial)

    try:
        run_editor(editor, draft)
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

    rewrite_with(draft, contents, mutate=fill_title)
    draft.rename(final_path)
    click.echo(str(final_path))
