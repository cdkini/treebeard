"""Tests for `om daily` (CLI integration — carryover rules live in test_todos)."""

from __future__ import annotations

import os
import pathlib

from click.testing import CliRunner

from om.cli import cli
from tests.conftest import FROZEN_LATER, FROZEN_NOW, EditorFake, write_cfg

FROZEN_FRONTMATTER = (
    "---\n"
    "title: 2026-05-07\n"
    "source: user\n"
    "created_at: 2026-05-07T14:23:05Z\n"
    "updated_at: 2026-05-07T14:23:05Z\n"
    "tags: [daily]\n"
    "---\n"
)
EMPTY_BODY = "\n### TODOs\n\n\n### Notes\n\n\n"


def append(payload: str) -> EditorFake:
    def _do(_ed: str, p: pathlib.Path) -> None:
        p.write_text(p.read_text(encoding="utf-8") + payload, encoding="utf-8")

    return _do


def _write_prior_daily(vault: pathlib.Path, iso: str, body: str) -> None:
    """Write a prior daily file with valid frontmatter."""
    text = (
        "---\n"
        f"title: {iso}\n"
        "source: user\n"
        f"created_at: {iso}T10:00:00Z\n"
        f"updated_at: {iso}T10:00:00Z\n"
        "tags: [daily]\n"
        "---\n" + body
    )
    (vault / f"{iso}.md").write_text(text, encoding="utf-8")


def test_help(runner: CliRunner) -> None:
    result = runner.invoke(cli, ["daily", "--help"])
    assert result.exit_code == 0, result.output
    assert "daily" in result.output


def test_creates_todays_file_with_daily_tag(
    runner: CliRunner,
    cfg_dir: pathlib.Path,
    vault: pathlib.Path,
    fake_editor: list[EditorFake],
    freeze_now: list,
    freeze_today: None,
) -> None:
    del freeze_now, freeze_today
    fake_editor.append(append("edited\n"))
    write_cfg(cfg_dir, vault)
    result = runner.invoke(cli, ["daily"])
    assert result.exit_code == 0, result.output
    path = vault / "2026-05-07.md"
    assert path.read_text(encoding="utf-8") == FROZEN_FRONTMATTER + EMPTY_BODY + "edited\n"
    assert str(path) in result.output


def test_discards_when_unchanged_and_no_carryover(
    runner: CliRunner,
    cfg_dir: pathlib.Path,
    vault: pathlib.Path,
    fake_editor: list[EditorFake],
    freeze_now: list,
    freeze_today: None,
) -> None:
    del freeze_now, freeze_today, fake_editor  # empty queue → no edit
    write_cfg(cfg_dir, vault)
    result = runner.invoke(cli, ["daily"])
    assert result.exit_code == 0, result.output
    assert not (vault / "2026-05-07.md").exists()
    assert "discarded empty note" in result.output


def test_reopens_existing_daily_preserving_tags(
    runner: CliRunner,
    cfg_dir: pathlib.Path,
    vault: pathlib.Path,
    fake_editor: list[EditorFake],
    freeze_now: list,
    freeze_today: None,
) -> None:
    del freeze_today
    freeze_now[:] = [FROZEN_LATER]
    path = vault / "2026-05-07.md"
    path.write_text(
        "---\n"
        "title: 2026-05-07\n"
        "source: user\n"
        "created_at: 2026-05-07T10:00:00Z\n"
        "updated_at: 2026-05-07T10:00:00Z\n"
        "tags: [daily, journal]\n"
        "---\n"
        "earlier thoughts\n",
        encoding="utf-8",
    )
    old = path.stat().st_mtime - 60
    os.utime(path, (old, old))

    fake_editor.append(append("more\n"))
    write_cfg(cfg_dir, vault)
    result = runner.invoke(cli, ["daily"])
    assert result.exit_code == 0, result.output
    text = path.read_text(encoding="utf-8")
    assert "tags: [daily, journal]\n" in text
    assert "created_at: 2026-05-07T10:00:00Z\n" in text
    assert "updated_at: 2026-05-07T15:00:00Z\n" in text
    assert text.endswith("earlier thoughts\nmore\n")


def test_carryover_from_prior_daily_is_injected(
    runner: CliRunner,
    cfg_dir: pathlib.Path,
    vault: pathlib.Path,
    fake_editor: list[EditorFake],
    freeze_now: list,
    freeze_today: None,
) -> None:
    """Wiring test only — carryover rules are tested in test_todos.py."""
    del freeze_now, freeze_today, fake_editor  # no edit; carryover keeps the file
    _write_prior_daily(vault, "2026-05-06", "- [ ] survives\n- [x] done\n")
    write_cfg(cfg_dir, vault)
    result = runner.invoke(cli, ["daily"])
    assert result.exit_code == 0, result.output
    text = (vault / "2026-05-07.md").read_text(encoding="utf-8")
    assert "- [ ] survives (from 05/06)" in text
    assert "done" not in text
    assert "discarded empty note" not in result.output


def test_does_not_recarry_when_today_already_exists(
    runner: CliRunner,
    cfg_dir: pathlib.Path,
    vault: pathlib.Path,
    fake_editor: list[EditorFake],
    freeze_now: list,
    freeze_today: None,
) -> None:
    del freeze_today
    freeze_now[:] = [FROZEN_NOW, FROZEN_LATER]
    _write_prior_daily(vault, "2026-05-06", "- [ ] yesterday's open task\n")
    today_path = vault / "2026-05-07.md"
    today_path.write_text(
        "---\n"
        "title: 2026-05-07\n"
        "source: user\n"
        "created_at: 2026-05-07T10:00:00Z\n"
        "updated_at: 2026-05-07T10:00:00Z\n"
        "tags: [daily]\n"
        "---\n"
        "\n### TODOs\n- [ ] today's task\n\n### Notes\n\nalready noted\n\n",
        encoding="utf-8",
    )
    old = today_path.stat().st_mtime - 60
    os.utime(today_path, (old, old))

    fake_editor.append(append(""))  # mtime moves but content unchanged after rewrite
    write_cfg(cfg_dir, vault)
    result = runner.invoke(cli, ["daily"])
    assert result.exit_code == 0, result.output
    text = today_path.read_text(encoding="utf-8")
    assert "yesterday's open task" not in text
    assert "today's task" in text


def test_errors_when_no_vault_configured(runner: CliRunner, tmp_path: pathlib.Path) -> None:
    empty_cfg = tmp_path / "empty"
    empty_cfg.mkdir()
    result = runner.invoke(cli, ["daily"])
    assert result.exit_code != 0
    assert "no vault configured" in result.output
