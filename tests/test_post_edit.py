"""Tests for `treebeard.post_edit`."""

from __future__ import annotations

import pathlib
from datetime import UTC, datetime

import pytest

from treebeard.frontmatter import Frontmatter
from treebeard.post_edit import (
    PostEditAbort,
    reconcile_filename,
    scratch_filename,
    slugify,
)

NOW = datetime(2026, 5, 7, 14, 23, 5, tzinfo=UTC)


def _write(
    path: pathlib.Path, title: str, *, tags: list[str] | None = None, body: str = ""
) -> None:
    fm = Frontmatter.new(title, NOW)
    if tags:
        fm.tags = list(tags)
    path.write_text(fm.serialize() + body, encoding="utf-8")


def test_slugify_basic() -> None:
    assert slugify("Hello World") == "hello-world"
    assert slugify("Sprint Planning!") == "sprint-planning"
    assert slugify("foo.md") == "foo"


def test_slugify_empty_raises() -> None:
    with pytest.raises(PostEditAbort, match="empty slug"):
        slugify("!!!")


def test_scratch_filename() -> None:
    assert scratch_filename(NOW) == "scratch-2026-05-07t14-23-05.md"


def test_renames_when_title_differs(tmp_path: pathlib.Path) -> None:
    path = tmp_path / "old-name.md"
    _write(path, "New Title")
    result = reconcile_filename(path, now=NOW)
    assert result == tmp_path / "new-title.md"
    assert result.exists()
    assert not path.exists()


def test_noop_when_slug_matches(tmp_path: pathlib.Path) -> None:
    path = tmp_path / "hello-world.md"
    _write(path, "Hello World")
    result = reconcile_filename(path, now=NOW)
    assert result == path
    assert path.exists()


def test_unparseable_frontmatter_is_noop(tmp_path: pathlib.Path) -> None:
    path = tmp_path / "raw.md"
    path.write_text("just a body\n", encoding="utf-8")
    assert reconcile_filename(path, now=NOW) == path


def test_daily_tag_protection_aborts(tmp_path: pathlib.Path) -> None:
    path = tmp_path / "2026-05-07.md"
    _write(path, "Sprint Retro", tags=["daily"])
    with pytest.raises(PostEditAbort, match="daily note filename is protected"):
        reconcile_filename(path, now=NOW)
    # File still on disk (the wrapper, not this function, restores contents).
    assert path.exists()


def test_daily_tag_no_rename_needed_is_noop(tmp_path: pathlib.Path) -> None:
    """A daily where the title already matches the date filename doesn't trip the guard."""
    path = tmp_path / "2026-05-07.md"
    _write(path, "2026-05-07", tags=["daily"])
    assert reconcile_filename(path, now=NOW) == path


def test_collision_aborts(tmp_path: pathlib.Path) -> None:
    existing = tmp_path / "foo.md"
    existing.write_text("existing\n", encoding="utf-8")

    path = tmp_path / "bar.md"
    _write(path, "Foo")
    with pytest.raises(PostEditAbort, match=r"would rename to foo\.md but it exists"):
        reconcile_filename(path, now=NOW)
    assert path.exists()
    assert existing.read_text(encoding="utf-8") == "existing\n"


def test_empty_title_on_scratch_stays_put(tmp_path: pathlib.Path) -> None:
    """An untitled scratch keeps its name; we don't churn the timestamp on every save."""
    path = tmp_path / "scratch-2026-05-07t14-23-05.md"
    _write(path, "")
    assert reconcile_filename(path, now=NOW) == path


def test_empty_title_on_non_scratch_renames_to_scratch(tmp_path: pathlib.Path) -> None:
    """A named note whose title is wiped becomes a scratch — rare, but consistent."""
    path = tmp_path / "old-name.md"
    _write(path, "")
    later = datetime(2026, 5, 7, 15, 0, 0, tzinfo=UTC)
    result = reconcile_filename(path, now=later)
    assert result == tmp_path / "scratch-2026-05-07t15-00-00.md"
    assert not path.exists()
