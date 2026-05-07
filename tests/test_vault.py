"""Tests for `om.vault.find_prior_daily`."""

from __future__ import annotations

import pathlib
from datetime import date

from om.vault import find_prior_daily

TODAY = date(2026, 5, 7)


def _touch(vault: pathlib.Path, name: str) -> pathlib.Path:
    path = vault / name
    path.write_text("placeholder\n", encoding="utf-8")
    return path


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
