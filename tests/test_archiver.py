"""Unit tests for `om.archiver` — exercises the helper directly to cover
edge cases (empty input, missing source) that the CLI-driven tests in
`test_archive.py` don't naturally hit."""

from __future__ import annotations

import pathlib
from datetime import UTC, datetime

import pytest

from om import archiver
from om.ui import OmError


def test_archive_paths_empty_list_is_noop(vault: pathlib.Path) -> None:
    """Empty input short-circuits before mkdir, so `.om/archive/` stays absent."""
    out = archiver.archive_paths(vault, [], now=datetime(2026, 5, 7, 14, 0, 0, tzinfo=UTC))
    assert out == []
    assert not (vault / ".om" / "archive").exists()


def test_archive_paths_missing_source_raises(vault: pathlib.Path) -> None:
    """A path that disappeared between picker and rename must surface as
    `OmError` rather than the raw FileNotFoundError from `rename`."""
    ghost = vault / "ghost.md"
    with pytest.raises(OmError, match="no longer exists"):
        archiver.archive_paths(vault, [ghost], now=datetime(2026, 5, 7, 14, 0, 0, tzinfo=UTC))


def test_archive_stamp_is_filename_safe() -> None:
    """No colons (FAT/Windows-hostile); 'Z' suffix marks UTC."""
    stamp = archiver.archive_stamp(datetime(2026, 5, 7, 14, 23, 5, tzinfo=UTC))
    assert stamp == "2026-05-07T14-23-05Z"
    assert ":" not in stamp


def test_archive_dir_is_under_dot_om(vault: pathlib.Path) -> None:
    assert archiver.archive_dir(vault) == vault / ".om" / "archive"
