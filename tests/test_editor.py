"""Tests for `om.editor` — `apply_post_edit`, the discard branch, and shims."""

from __future__ import annotations

import pathlib

import pytest

from om.editor import apply_post_edit, edit_with_initial, reopen
from om.frontmatter import Frontmatter
from om.post_edit import PostEditAbort
from tests.conftest import FROZEN_LATER, FROZEN_NOW, EditorFake


def _seed(
    path: pathlib.Path, title: str, *, tags: list[str] | None = None, body: str = "body\n"
) -> None:
    fm = Frontmatter.new(title, FROZEN_NOW)
    if tags:
        fm.tags = list(tags)
    path.write_text(fm.serialize() + body, encoding="utf-8")


def test_apply_post_edit_bumps_updated_at(tmp_path: pathlib.Path) -> None:
    path = tmp_path / "todo.md"
    _seed(path, "todo")
    final = apply_post_edit(path, now=FROZEN_LATER)
    assert final == path
    text = path.read_text(encoding="utf-8")
    assert "updated_at: 2026-05-07T15:00:00Z\n" in text


def test_apply_post_edit_renames_when_title_changes(tmp_path: pathlib.Path) -> None:
    path = tmp_path / "old-name.md"
    _seed(path, "New Name")
    final = apply_post_edit(path, now=FROZEN_NOW)
    assert final == tmp_path / "new-name.md"
    assert final.exists()
    assert not path.exists()


def test_apply_post_edit_raises_on_daily_tag_rename(tmp_path: pathlib.Path) -> None:
    path = tmp_path / "2026-05-07.md"
    _seed(path, "Sprint Retro", tags=["daily"])
    with pytest.raises(PostEditAbort, match="daily note filename is protected"):
        apply_post_edit(path, now=FROZEN_NOW)
    # Caller (the close hook) decides what to do with the abort. The
    # bumped updated_at has already been written; that's intentional —
    # the rename guard is what raised, not the bump.
    assert path.exists()


def test_apply_post_edit_raises_on_collision(tmp_path: pathlib.Path) -> None:
    (tmp_path / "foo.md").write_text("existing\n", encoding="utf-8")
    path = tmp_path / "bar.md"
    _seed(path, "Foo")
    with pytest.raises(PostEditAbort, match=r"would rename to foo\.md but it exists"):
        apply_post_edit(path, now=FROZEN_NOW)
    assert path.exists()
    assert (tmp_path / "foo.md").read_text(encoding="utf-8") == "existing\n"


def test_apply_post_edit_noop_on_unparseable_frontmatter(tmp_path: pathlib.Path) -> None:
    path = tmp_path / "raw.md"
    path.write_text("just a body\n", encoding="utf-8")
    final = apply_post_edit(path, now=FROZEN_NOW)
    assert final == path
    assert path.read_text(encoding="utf-8") == "just a body\n"


def test_reopen_runs_editor(
    tmp_path: pathlib.Path,
    fake_editor: list[EditorFake],
    freeze_now: list,
) -> None:
    """`reopen` is now a thin shim around `run_editor`. Verify it dispatches."""
    del freeze_now
    path = tmp_path / "todo.md"
    _seed(path, "todo")

    def append(_ed: str, p: pathlib.Path) -> None:
        p.write_text(p.read_text(encoding="utf-8") + "more\n", encoding="utf-8")

    fake_editor.append(append)
    reopen(path, "vim")
    assert path.read_text(encoding="utf-8").endswith("body\nmore\n")


def test_edit_with_initial_seeds_and_keeps_when_user_edits(
    tmp_path: pathlib.Path,
    fake_editor: list[EditorFake],
    freeze_now: list,
) -> None:
    """User touched the file → file stays; close hook will reconcile later."""
    del freeze_now
    path = tmp_path / "scratch-2026-05-07t14-23-05.md"
    initial = Frontmatter.new("", FROZEN_NOW).serialize() + "\n\n"

    def add_title(_ed: str, p: pathlib.Path) -> None:
        text = p.read_text(encoding="utf-8")
        p.write_text(text.replace("title: \n", "title: My Idea\n", 1), encoding="utf-8")

    fake_editor.append(add_title)
    edit_with_initial(path, initial, "vim")
    assert path.exists()
    assert "title: My Idea\n" in path.read_text(encoding="utf-8")


def test_edit_with_initial_discards_unchanged(
    tmp_path: pathlib.Path,
    fake_editor: list[EditorFake],
    freeze_now: list,
) -> None:
    del freeze_now, fake_editor
    path = tmp_path / "foo.md"
    initial = Frontmatter.new("foo", FROZEN_NOW).serialize() + "\n\n"
    edit_with_initial(path, initial, "vim")
    assert not path.exists()


@pytest.mark.parametrize("with_keep", [False, True])
def test_edit_with_initial_keep_when_unchanged(
    tmp_path: pathlib.Path,
    fake_editor: list[EditorFake],
    freeze_now: list,
    with_keep: bool,
) -> None:
    """`keep_when_unchanged=True` keeps the file even if the editor was a no-op."""
    del freeze_now, fake_editor
    path = tmp_path / "keep.md"
    initial = Frontmatter.new("keep", FROZEN_NOW).serialize() + "\n\n"
    edit_with_initial(path, initial, "vim", keep_when_unchanged=with_keep)
    assert path.exists() == with_keep
