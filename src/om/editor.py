"""Editor invocation and per-file mutation helpers for note files.

Reusable across commands that drop the user into an editor on a markdown
file with frontmatter. Keeps the per-command modules thin.

Post-edit work (bumping `updated_at`, reconciling the filename against
the title) is *not* done here — `cli._on_close` runs a porcelain-based
sweep over the vault after every subcommand and applies post-edit to
every dirty `.md` at the vault root, including files the user
side-jumped to via wikilinks / `:e` / `gf`. The `apply_post_edit` free
function below is what the close hook calls per file; it's exposed here
because its body lives next to the editor invocation it pairs with.
"""

from __future__ import annotations

import pathlib
import subprocess
from collections.abc import Callable
from datetime import datetime

from om import dependencies, ui
from om.frontmatter import Frontmatter, Source, has_source, split_document, write_note
from om.post_edit import reconcile_filename
from om.ui import OmError


def run_editor(editor: str, path: pathlib.Path, *, start_line: int | None = None) -> None:
    """Run `editor + path`. Raises `ClickException` on non-zero exit.

    The bare `+` arg lands vim/nvim's cursor at the last line of the file;
    `+<N>` lands it at line N. Pass `start_line` from grep-style callers
    that already know which line is interesting.
    """
    dependencies.check_editor(editor)

    line_arg = f"+{start_line}" if start_line is not None else "+"
    try:
        subprocess.run([editor, line_arg, str(path)], check=True)
    except subprocess.CalledProcessError as exc:
        raise OmError(f"editor exited with status {exc.returncode}") from exc


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
    write_note(path, fm, body)


def apply_post_edit(path: pathlib.Path, *, now: datetime) -> pathlib.Path:
    """Bump `updated_at` and reconcile the filename against the title.

    Returns the final path (possibly renamed by `reconcile_filename`).
    Re-raises `PostEditAbort` so the caller (the CLI close hook) can warn
    and continue rather than aborting the whole sweep on one bad file.

    No-op when the file has no parseable frontmatter (`rewrite_with`
    short-circuits) — `reconcile_filename` has the same property.

    Imported notes (`source: import`) opt out of the `updated_at` bump:
    their timestamp mirrors the upstream system so it can drive the
    importer's idempotency comparison. We still run filename reconcile.
    """
    contents = path.read_text(encoding="utf-8")
    parsed = split_document(contents)
    if parsed is None:
        # No frontmatter — nothing to bump, nothing to reconcile.
        return path
    fm, _ = parsed
    if has_source(fm, Source.IMPORT):
        # Imported notes are owned end-to-end by the importer: their
        # `updated_at` mirrors the upstream system (so it can drive the
        # importer's idempotency check), and their filename carries a
        # source/date prefix that the title-derived slug would erase.
        return path

    def bump(fm: Frontmatter) -> None:
        fm.updated_at = now

    rewrite_with(path, contents, mutate=bump)
    return reconcile_filename(path, now=now)


def edit_with_initial(
    path: pathlib.Path,
    initial: str,
    editor: str,
    *,
    keep_when_unchanged: bool = False,
) -> None:
    """Seed `path` with `initial`, run the editor, then either discard
    (when the user didn't touch the file) or leave the file in place for
    the close hook's post-edit sweep to bump and reconcile.

    `keep_when_unchanged=True` skips the discard step — useful when the
    seeded content is already meaningful (e.g., daily carry-forward).

    On editor failure, the half-created file is unlinked and the error
    propagates.
    """
    path.write_text(initial, encoding="utf-8")
    try:
        run_editor(editor, path)
    except OmError:
        path.unlink(missing_ok=True)
        raise

    contents = path.read_text(encoding="utf-8")
    if contents == initial and not keep_when_unchanged:
        path.unlink()
        ui.info(f"discarded empty note: {path}")


def reopen(path: pathlib.Path, editor: str, *, start_line: int | None = None) -> None:
    """Reopen an existing note in the editor.

    Thin wrapper around `run_editor` — bumping `updated_at` and
    reconciling the filename is now the close hook's job.
    """
    run_editor(editor, path, start_line=start_line)
