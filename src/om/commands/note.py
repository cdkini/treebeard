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

from om.config import load_config
from om.editor import edit_with_initial, reopen
from om.frontmatter import Frontmatter
from om.post_edit import PostEditAbort, scratch_filename, slugify
from om.ui import OmError


def _now_utc() -> datetime:
    return datetime.now(UTC)


@click.command("note")
@click.argument("name", required=False)
def command(name: str | None) -> None:
    """Create or open a markdown note in the vault."""
    cfg = load_config()

    now = _now_utc()

    if name is None:
        create_scratch(cfg.vault, now, cfg.editor)
        return

    try:
        slug = slugify(name)
    except PostEditAbort as exc:
        raise OmError(str(exc)) from exc
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

    Returns the seed path. The file may be renamed later by the close
    hook's post-edit sweep if the user changed the title.
    """
    path = vault / f"{slug}.md"
    fm = Frontmatter.new(title, now)
    if tags:
        fm.tags = list(tags)
    edit_with_initial(path, fm.serialize() + "\n\n", editor)
    return path


def create_scratch(vault: pathlib.Path, now: datetime, editor: str) -> pathlib.Path:
    """Create `vault/scratch-<timestamp>.md` and open the editor.

    On close, the post-edit sweep will rename the file to a
    slugified-title name if the user filled in a title; otherwise it
    stays as scratch.
    """
    path = vault / scratch_filename(now)
    fm = Frontmatter.new("", now)
    edit_with_initial(path, fm.serialize() + "\n\n", editor)
    return path
