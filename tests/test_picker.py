"""Unit tests for `treebeard.picker` — row formatting + previewer ladder shared
by `tb open` and `tb archive`."""

from __future__ import annotations

import pathlib
import time

import pytest

from treebeard import dependencies as deps
from treebeard import picker


def _write_note(path: pathlib.Path, title: str, body: str = "body\n") -> None:
    path.write_text(
        "---\n"
        f"title: {title}\n"
        "source: user\n"
        "created_at: 2026-05-07T14:23:05Z\n"
        "updated_at: 2026-05-07T14:23:05Z\n"
        "tags: []\n"
        "---\n"
        f"{body}",
        encoding="utf-8",
    )


def test_format_line_uses_title(vault: pathlib.Path) -> None:
    path = vault / "note.md"
    _write_note(path, "Hello World")
    line = picker.format_line(path, time.time())
    display, abspath = line.split("\t", 1)
    assert display.startswith("Hello World")
    assert abspath == str(path)


def test_format_line_pads_short_title(vault: pathlib.Path) -> None:
    """Short titles are right-padded to TITLE_WIDTH so the ago column
    aligns across rows."""
    path = vault / "short.md"
    _write_note(path, "Hi")
    line = picker.format_line(path, time.time())
    display = line.split("\t", 1)[0]
    title_field = display[: picker.TITLE_WIDTH]
    assert title_field == "Hi" + " " * (picker.TITLE_WIDTH - 2)


def test_format_line_truncates_long_title(vault: pathlib.Path) -> None:
    """Titles longer than TITLE_WIDTH are truncated with an ellipsis."""
    path = vault / "long.md"
    _write_note(path, "x" * 100)
    line = picker.format_line(path, time.time())
    display = line.split("\t", 1)[0]
    title_field = display.split("  ", 1)[0]
    assert len(title_field) == picker.TITLE_WIDTH
    assert title_field.endswith("…")


def test_format_line_falls_back_to_stem_without_frontmatter(vault: pathlib.Path) -> None:
    """No frontmatter at all → use the filename stem as the title."""
    path = vault / "stemmed.md"
    path.write_text("just a body\n", encoding="utf-8")
    line = picker.format_line(path, time.time())
    display = line.split("\t", 1)[0]
    assert display.startswith("stemmed")


def test_preview_cmd_prefers_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    """User's configured previewer wins when available."""
    monkeypatch.setattr(deps.shutil, "which", lambda name: f"/usr/bin/{name}")
    cmd = picker.preview_cmd("bat")
    assert cmd.startswith("bat ")


def test_preview_cmd_falls_through_when_configured_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Configured `glow` not on PATH → walk the rest of the ladder; bat is
    next available."""

    def which(name: str) -> str | None:
        return None if name == "glow" else f"/usr/bin/{name}"

    monkeypatch.setattr(deps.shutil, "which", which)
    cmd = picker.preview_cmd("glow")
    assert cmd.startswith("bat ")


def test_preview_cmd_terminates_in_cat(monkeypatch: pytest.MonkeyPatch) -> None:
    """Nothing on PATH → defaults to `cat` so the picker still has a
    preview command (cat is part of coreutils, always present in
    practice)."""
    monkeypatch.setattr(deps.shutil, "which", lambda _name: None)
    cmd = picker.preview_cmd("bat")
    assert cmd.startswith("cat ")
