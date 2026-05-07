"""Editor invocation and the write-edit-rewrite lifecycle for note files.

Reusable across commands that drop the user into an editor on a markdown
file with frontmatter. Keeps the per-command modules thin.

The `edit_atomically` context manager wraps every edit so post-edit
invariants can revert the file from a snapshot by raising
`PostEditAbort`. v1 invariant: title-canonical filenames (see
`om.post_edit.reconcile_filename`).
"""

from __future__ import annotations

import pathlib
import subprocess
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime

from om import ui
from om.frontmatter import Frontmatter, split_document
from om.post_edit import PostEditAbort, reconcile_filename
from om.ui import OmError


def _now_utc() -> datetime:
    return datetime.now(UTC)


def run_editor(editor: str, path: pathlib.Path, *, start_line: int | None = None) -> None:
    """Run `editor + path`. Raises `ClickException` on non-zero exit.

    The bare `+` arg lands vim/nvim's cursor at the last line of the file;
    `+<N>` lands it at line N. Pass `start_line` from grep-style callers
    that already know which line is interesting.
    """
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
    path.write_text(fm.serialize() + body, encoding="utf-8")


@contextmanager
def edit_atomically(path: pathlib.Path) -> Iterator[list[bool]]:
    """Snapshot `path` on entry; on `PostEditAbort`, restore and echo.

    Any post-edit step inside the block can raise `PostEditAbort` to
    signal that the edit violated an invariant. The wrapper restores the
    pre-edit state and prints a user-facing message — the editor has
    already exited at this point.

    Yields a single-element list whose value flips to `True` when an
    abort happened, so callers can suppress success output.

    Invariant for v1: post-edit steps that *rename* the file must run
    last and must raise *before* the rename when they want to abort.
    Otherwise the snapshot/path pair won't match what's on disk.
    """
    snapshot = path.read_bytes() if path.exists() else None
    aborted = [False]
    try:
        yield aborted
    except PostEditAbort as exc:
        aborted[0] = True
        if snapshot is None:
            path.unlink(missing_ok=True)
        else:
            path.write_bytes(snapshot)
        ui.warn(f"edit reverted: {exc}")


def edit_with_initial(
    path: pathlib.Path,
    initial: str,
    editor: str,
    *,
    keep_when_unchanged: bool = False,
) -> pathlib.Path:
    """Seed `path` with `initial`, run the editor, then either discard
    (when the user didn't touch the file) or re-serialize the frontmatter
    and reconcile the filename against the title.

    `keep_when_unchanged=True` skips the discard step — useful when the
    seeded content is already meaningful (e.g., daily carry-forward).

    Returns the final path (possibly renamed by `reconcile_filename`).
    """
    final_path = path
    discarded = False
    with edit_atomically(path) as aborted:
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
            discarded = True
        else:
            rewrite_with(path, contents, mutate=lambda fm: None)
            final_path = reconcile_filename(path, now=_now_utc())
    if not aborted[0] and not discarded:
        ui.path(str(final_path))
    return final_path


def reopen(path: pathlib.Path, editor: str, *, start_line: int | None = None) -> pathlib.Path:
    """Reopen an existing note. Bumps `updated_at` only if the user
    actually changed the file during the edit, then reconciles the
    filename against the title.

    Returns the final path (possibly renamed).
    """
    mtime_before = path.stat().st_mtime_ns
    final_path = path
    with edit_atomically(path) as aborted:
        run_editor(editor, path, start_line=start_line)
        mtime_after = path.stat().st_mtime_ns
        if mtime_after != mtime_before:
            contents = path.read_text(encoding="utf-8")
            bump_ts = _now_utc()

            def bump(fm: Frontmatter) -> None:
                fm.updated_at = bump_ts

            rewrite_with(path, contents, mutate=bump)
            final_path = reconcile_filename(path, now=bump_ts)
    if not aborted[0]:
        ui.path(str(final_path))
    return final_path
