"""`om note [NAME]` — create or open a markdown note in the configured vault.

With no NAME, creates an untitled `scratch-<timestamp>.md`. The user can
add a title in the editor; on close, `reconcile_filename` (in
`om.post_edit`) renames the file to match the slugified title. Files
that stay untitled keep their `scratch-*` name.
"""

from __future__ import annotations

import pathlib
from datetime import UTC, datetime

import click

from om.config import (
    CONFIG_FILENAME,
    DEFAULT_CONFIG_DIR,
    load_config,
)
from om.editor import edit_with_initial, reopen
from om.frontmatter import Frontmatter
from om.post_edit import PostEditAbort, scratch_filename, slugify


def _now_utc() -> datetime:
    return datetime.now(UTC)


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
    """Create or open a markdown note in the vault."""
    ctx.ensure_object(dict)["config_dir"] = config_dir
    cfg = load_config(config_dir)

    now = _now_utc()

    if name is None:
        create_scratch(cfg.vault, now, cfg.editor)
        return

    try:
        slug = slugify(name)
    except PostEditAbort as exc:
        raise click.ClickException(str(exc)) from exc
    path = cfg.vault / f"{slug}.md"
    if path.exists():
        reopen(path, cfg.editor)
        return

    stripped = name.removesuffix(".md")
    create_named_note(cfg.vault, slug, stripped, now, cfg.editor)


def create_named_note(
    vault: pathlib.Path,
    slug: str,
    title: str,
    now: datetime,
    editor: str,
    *,
    tags: list[str] | None = None,
) -> pathlib.Path:
    """Create `vault/{slug}.md` with frontmatter and open the editor.

    Returns the final path (post-rename, since `edit_with_initial` may
    rename the file if the user changed the title in the editor).
    """
    path = vault / f"{slug}.md"
    fm = Frontmatter.new(title, now)
    if tags:
        fm.tags = list(tags)
    return edit_with_initial(path, fm.serialize() + "\n\n", editor)


def create_scratch(vault: pathlib.Path, now: datetime, editor: str) -> pathlib.Path:
    """Create `vault/scratch-<timestamp>.md` and open the editor.

    On close, the rename hook will move the file to a slugified-title
    name if the user filled in a title; otherwise it stays as scratch.
    """
    path = vault / scratch_filename(now)
    fm = Frontmatter.new("", now)
    return edit_with_initial(path, fm.serialize() + "\n\n", editor)
