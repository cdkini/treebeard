"""Tests for `treebeard.vault`."""

from __future__ import annotations

import os
import pathlib
from datetime import date

from treebeard.vault import find_prior_daily, list_recent_notes

TODAY = date(2026, 5, 7)


def _touch(vault: pathlib.Path, name: str) -> pathlib.Path:
    path = vault / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("placeholder\n", encoding="utf-8")
    return path


def _set_mtime(path: pathlib.Path, seconds_ago: int) -> None:
    target = path.stat().st_mtime - seconds_ago
    os.utime(path, (target, target))


def test_returns_none_on_empty_vault(tmp_path: pathlib.Path) -> None:
    assert find_prior_daily(tmp_path, TODAY) is None


def test_picks_most_recent_prior_daily(tmp_path: pathlib.Path) -> None:
    _touch(tmp_path, "2026-04-01.md")
    expected = _touch(tmp_path, "2026-05-06.md")
    result = find_prior_daily(tmp_path, TODAY)
    assert result == (expected, date(2026, 5, 6))


def test_skips_files_dated_today_or_later(tmp_path: pathlib.Path) -> None:
    _touch(tmp_path, "2026-05-07.md")
    _touch(tmp_path, "2026-06-01.md")
    assert find_prior_daily(tmp_path, TODAY) is None


def test_ignores_non_date_named_markdown(tmp_path: pathlib.Path) -> None:
    _touch(tmp_path, "notes.md")
    expected = _touch(tmp_path, "2026-05-06.md")
    result = find_prior_daily(tmp_path, TODAY)
    assert result == (expected, date(2026, 5, 6))


def test_ignores_invalid_date_shaped_names(tmp_path: pathlib.Path) -> None:
    _touch(tmp_path, "2026-13-99.md")  # bad month/day
    assert find_prior_daily(tmp_path, TODAY) is None


def test_list_recent_notes_orders_by_mtime_desc(tmp_path: pathlib.Path) -> None:
    a = _touch(tmp_path, "a.md")
    b = _touch(tmp_path, "b.md")
    c = _touch(tmp_path, "c.md")
    _set_mtime(a, 300)
    _set_mtime(b, 60)
    _set_mtime(c, 0)
    assert list_recent_notes(tmp_path) == [c, b, a]


def test_list_recent_notes_caps_at_limit(tmp_path: pathlib.Path) -> None:
    for i in range(15):
        path = _touch(tmp_path, f"note-{i}.md")
        _set_mtime(path, 15 - i)
    result = list_recent_notes(tmp_path, limit=5)
    assert len(result) == 5
    assert [p.name for p in result] == [f"note-{i}.md" for i in (14, 13, 12, 11, 10)]


def test_list_recent_notes_empty_vault(tmp_path: pathlib.Path) -> None:
    assert list_recent_notes(tmp_path) == []


def test_list_recent_notes_skips_subdirs(tmp_path: pathlib.Path) -> None:
    """Vaults are flat. Markdown files in subdirectories — including
    tooling dirs like `.claude/` (where `tb chat`'s project memory
    lives) — are not user notes and must not appear in `tb open`."""
    nested = _touch(tmp_path, "subdir/inner.md")
    claude_md = _touch(tmp_path, ".claude/CLAUDE.md")
    flat = _touch(tmp_path, "flat.md")
    _set_mtime(flat, 60)
    _set_mtime(nested, 0)
    _set_mtime(claude_md, 0)
    assert list_recent_notes(tmp_path) == [flat]
