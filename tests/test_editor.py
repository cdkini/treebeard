"""Tests for `om.editor` — the atomic wrapper, rename, and revert."""

from __future__ import annotations

import os
import pathlib

import pytest

from om.editor import edit_atomically, edit_with_initial, reopen
from om.frontmatter import Frontmatter
from om.post_edit import PostEditAbort
from tests.conftest import FROZEN_NOW, EditorFake


def _seed(
    path: pathlib.Path, title: str, *, tags: list[str] | None = None, body: str = "body\n"
) -> None:
    fm = Frontmatter.new(title, FROZEN_NOW)
    if tags:
        fm.tags = list(tags)
    path.write_text(fm.serialize() + body, encoding="utf-8")


def _backdate(path: pathlib.Path) -> None:
    old = path.stat().st_mtime - 60
    os.utime(path, (old, old))


def test_edit_atomically_restores_on_abort(tmp_path: pathlib.Path) -> None:
    path = tmp_path / "foo.md"
    path.write_text("original\n", encoding="utf-8")
    with edit_atomically(path) as aborted:
        path.write_text("dirty\n", encoding="utf-8")
        raise PostEditAbort("nope")
    assert aborted[0] is True
    assert path.read_text(encoding="utf-8") == "original\n"


def test_edit_atomically_deletes_when_no_snapshot(tmp_path: pathlib.Path) -> None:
    path = tmp_path / "new.md"
    with edit_atomically(path) as aborted:
        path.write_text("created\n", encoding="utf-8")
        raise PostEditAbort("nope")
    assert aborted[0] is True
    assert not path.exists()


def test_edit_atomically_passes_through_when_no_abort(tmp_path: pathlib.Path) -> None:
    path = tmp_path / "foo.md"
    path.write_text("a\n", encoding="utf-8")
    with edit_atomically(path) as aborted:
        path.write_text("b\n", encoding="utf-8")
    assert aborted[0] is False
    assert path.read_text(encoding="utf-8") == "b\n"


def test_reopen_renames_when_title_changes(
    tmp_path: pathlib.Path,
    fake_editor: list[EditorFake],
    freeze_now: list,
) -> None:
    del freeze_now
    path = tmp_path / "old-name.md"
    _seed(path, "old name")
    _backdate(path)

    def retitle(_ed: str, p: pathlib.Path) -> None:
        text = p.read_text(encoding="utf-8")
        p.write_text(text.replace("title: old name", "title: New Name", 1), encoding="utf-8")

    fake_editor.append(retitle)
    final = reopen(path, "vim")
    assert final == tmp_path / "new-name.md"
    assert final.exists()
    assert not path.exists()


def test_reopen_reverts_when_daily_rename_attempted(
    tmp_path: pathlib.Path,
    fake_editor: list[EditorFake],
    freeze_now: list,
) -> None:
    del freeze_now
    path = tmp_path / "2026-05-07.md"
    _seed(path, "2026-05-07", tags=["daily"])
    _backdate(path)
    original = path.read_bytes()

    def retitle(_ed: str, p: pathlib.Path) -> None:
        text = p.read_text(encoding="utf-8")
        p.write_text(text.replace("title: 2026-05-07", "title: Sprint Retro", 1), encoding="utf-8")

    fake_editor.append(retitle)
    final = reopen(path, "vim")
    assert final == path  # unchanged
    assert path.read_bytes() == original


def test_reopen_reverts_on_collision(
    tmp_path: pathlib.Path,
    fake_editor: list[EditorFake],
    freeze_now: list,
) -> None:
    del freeze_now
    other = tmp_path / "foo.md"
    other.write_text("existing\n", encoding="utf-8")

    path = tmp_path / "bar.md"
    _seed(path, "bar")
    _backdate(path)
    original = path.read_bytes()

    def retitle(_ed: str, p: pathlib.Path) -> None:
        text = p.read_text(encoding="utf-8")
        p.write_text(text.replace("title: bar", "title: Foo", 1), encoding="utf-8")

    fake_editor.append(retitle)
    final = reopen(path, "vim")
    assert final == path
    assert path.read_bytes() == original
    assert other.read_text(encoding="utf-8") == "existing\n"


def test_edit_with_initial_renames_to_slug(
    tmp_path: pathlib.Path,
    fake_editor: list[EditorFake],
    freeze_now: list,
) -> None:
    del freeze_now
    path = tmp_path / "scratch-2026-05-07t14-23-05.md"
    initial = Frontmatter.new("", FROZEN_NOW).serialize() + "\n\n"

    def add_title(_ed: str, p: pathlib.Path) -> None:
        text = p.read_text(encoding="utf-8")
        p.write_text(text.replace("title: \n", "title: My Idea\n", 1), encoding="utf-8")

    fake_editor.append(add_title)
    final = edit_with_initial(path, initial, "vim")
    assert final == tmp_path / "my-idea.md"
    assert final.exists()
    assert not path.exists()


def test_edit_with_initial_discards_unchanged(
    tmp_path: pathlib.Path,
    fake_editor: list[EditorFake],
    freeze_now: list,
) -> None:
    del freeze_now, fake_editor
    path = tmp_path / "foo.md"
    initial = Frontmatter.new("foo", FROZEN_NOW).serialize() + "\n\n"
    final = edit_with_initial(path, initial, "vim")
    assert final == path
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
