"""Tests for `om.timefmt.humanize_mtime`."""

from __future__ import annotations

import pytest

from om.timefmt import humanize_mtime


@pytest.mark.parametrize(
    ("seconds_ago", "expected"),
    [
        (-5, "just now"),
        (0, "just now"),
        (1, "just now"),
        (59, "just now"),
        (60, "1m ago"),
        (61, "1m ago"),
        (119, "1m ago"),
        (120, "2m ago"),
        (3599, "59m ago"),
        (3600, "1h ago"),
        (7200, "2h ago"),
        (86399, "23h ago"),
        (86400, "1d ago"),
        (172800, "2d ago"),
        (6 * 86400, "6d ago"),
        (7 * 86400, "1w ago"),
        (8 * 86400, "1w ago"),
        (14 * 86400, "2w ago"),
        (52 * 7 * 86400, "52w ago"),
    ],
)
def test_humanize_mtime(seconds_ago: float, expected: str) -> None:
    assert humanize_mtime(seconds_ago) == expected
