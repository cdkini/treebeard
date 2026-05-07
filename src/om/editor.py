"""Editor invocation and the write-edit-rewrite lifecycle for note files.

Reusable across commands that drop the user into an editor on a markdown
file with frontmatter. Keeps the per-command modules thin.
"""

from __future__ import annotations

import pathlib
import subprocess
from collections.abc import Callable
from datetime import UTC, datetime

import click

from om.frontmatter import Frontmatter, split_document


def _now_utc() -> datetime:
    return datetime.now(UTC)


def run_editor(editor: str, path: pathlib.Path) -> None:
    """Run `editor + path`. Raises `ClickException` on non-zero exit.

    The bare `+` arg lands vim/nvim's cursor at the last line of the file.
    """
    try:
        subprocess.run([editor, "+", str(path)], check=True)
    except subprocess.CalledProcessError as exc:
        raise click.ClickException(f"editor exited with status {exc.returncode}") from exc


def rewrite_with(path: pathlib.Path, contents: str, mutate: Callable[[Frontmatter], None]) -> None:
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


def edit_with_initial(
    path: pathlib.Path,
    initial: str,
    editor: str,
    *,
    keep_when_unchanged: bool = False,
) -> None:
    """Seed `path` with `initial`, run the editor, then either discard
    (when the user didn't touch the file) or re-serialize the frontmatter.

    `keep_when_unchanged=True` skips the discard step — useful when the
    seeded content is already meaningful (e.g., daily carry-forward).
    """
    path.write_text(initial, encoding="utf-8")
    try:
        run_editor(editor, path)
    except click.ClickException:
        path.unlink(missing_ok=True)
        raise

    contents = path.read_text(encoding="utf-8")
    if contents == initial and not keep_when_unchanged:
        path.unlink()
        click.echo(f"discarded empty note: {path}")
        return

    rewrite_with(path, contents, mutate=lambda fm: None)
    click.echo(str(path))


def reopen(path: pathlib.Path, editor: str) -> None:
    """Reopen an existing note. Bumps `updated_at` only if the user
    actually changed the file during the edit."""
    mtime_before = path.stat().st_mtime_ns
    run_editor(editor, path)
    mtime_after = path.stat().st_mtime_ns
    if mtime_after != mtime_before:
        contents = path.read_text(encoding="utf-8")
        bump_ts = _now_utc()

        def bump(fm: Frontmatter) -> None:
            fm.updated_at = bump_ts

        rewrite_with(path, contents, mutate=bump)
    click.echo(str(path))
