"""Tests for `om daily`."""

from __future__ import annotations

import os
import stat
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from click.testing import CliRunner

from om.cli import cli
from om.commands import daily as daily_cmd
from om.commands import note as note_cmd

FROZEN_NOW = datetime(2026, 5, 7, 14, 23, 5, tzinfo=UTC)
FROZEN_LATER = datetime(2026, 5, 7, 15, 0, 0, tzinfo=UTC)
FROZEN_TODAY = date(2026, 5, 7)


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    v = tmp_path / "vault"
    (v / ".om").mkdir(parents=True)
    return v


@pytest.fixture
def cfg_dir(tmp_path: Path, vault: Path) -> Path:
    d = tmp_path / "cfg"
    d.mkdir()
    (d / "config.toml").write_text(f'vault = "{vault}"\n', encoding="utf-8")
    return d


@pytest.fixture
def freeze_clock(monkeypatch: pytest.MonkeyPatch) -> list[datetime]:
    """Freeze `_now_utc` (in note module) and `_today_local` (in daily module)."""
    queue: list[datetime] = [FROZEN_NOW]

    def fake_now() -> datetime:
        return queue.pop(0) if len(queue) > 1 else queue[0]

    monkeypatch.setattr(note_cmd, "_now_utc", fake_now)
    monkeypatch.setattr(daily_cmd, "_now_utc", fake_now)
    monkeypatch.setattr(daily_cmd, "_today_local", lambda: FROZEN_TODAY)
    return queue


def _make_script(tmp_path: Path, body: str, name: str = "edit.sh") -> Path:
    script = tmp_path / name
    script.write_text(f"#!/bin/sh\n{body}\n", encoding="utf-8")
    script.chmod(script.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return script


def _appender(tmp_path: Path, payload: str = "edited\n", name: str = "appender.sh") -> Path:
    return _make_script(tmp_path, f'printf "%s" "{payload}" >> "$1"', name=name)


def test_help(runner: CliRunner) -> None:
    result = runner.invoke(cli, ["daily", "--help"])
    assert result.exit_code == 0, result.output
    assert "daily" in result.output


def test_creates_todays_file_with_daily_tag(
    runner: CliRunner,
    cfg_dir: Path,
    vault: Path,
    tmp_path: Path,
    freeze_clock: list[datetime],
) -> None:
    del freeze_clock
    script = _appender(tmp_path)
    result = runner.invoke(
        cli,
        ["daily", "--config-dir", str(cfg_dir)],
        env={"EDITOR": str(script)},
    )
    assert result.exit_code == 0, result.output
    path = vault / "2026-05-07.md"
    assert path.exists()
    text = path.read_text(encoding="utf-8")
    expected_frontmatter = (
        "---\n"
        "title: 2026-05-07\n"
        "source: user\n"
        "created_at: 2026-05-07T14:23:05Z\n"
        "updated_at: 2026-05-07T14:23:05Z\n"
        "tags: [daily]\n"
        "---\n"
    )
    assert text == expected_frontmatter + "\nedited\n"
    assert str(path) in result.output


def test_discards_when_unchanged(
    runner: CliRunner, cfg_dir: Path, vault: Path, freeze_clock: list[datetime]
) -> None:
    del freeze_clock
    result = runner.invoke(
        cli,
        ["daily", "--config-dir", str(cfg_dir)],
        env={"EDITOR": "true"},
    )
    assert result.exit_code == 0, result.output
    assert not (vault / "2026-05-07.md").exists()
    assert "discarded empty note" in result.output


def test_reopens_existing_daily_note_preserving_tags(
    runner: CliRunner,
    cfg_dir: Path,
    vault: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(daily_cmd, "_today_local", lambda: FROZEN_TODAY)
    queue: list[datetime] = [FROZEN_NOW, FROZEN_LATER]

    def fake_now() -> datetime:
        return queue.pop(0) if len(queue) > 1 else queue[0]

    monkeypatch.setattr(note_cmd, "_now_utc", fake_now)
    monkeypatch.setattr(daily_cmd, "_now_utc", fake_now)

    path = vault / "2026-05-07.md"
    original = (
        "---\n"
        "title: 2026-05-07\n"
        "source: user\n"
        "created_at: 2026-05-07T10:00:00Z\n"
        "updated_at: 2026-05-07T10:00:00Z\n"
        "tags: [daily, journal]\n"
        "---\n"
        "earlier thoughts\n"
    )
    path.write_text(original, encoding="utf-8")
    old = path.stat().st_mtime - 60
    os.utime(path, (old, old))

    script = _appender(tmp_path, payload="more\n")
    result = runner.invoke(
        cli,
        ["daily", "--config-dir", str(cfg_dir)],
        env={"EDITOR": str(script)},
    )
    assert result.exit_code == 0, result.output
    text = path.read_text(encoding="utf-8")
    assert "tags: [daily, journal]\n" in text
    assert "created_at: 2026-05-07T10:00:00Z\n" in text
    assert "updated_at: 2026-05-07T15:00:00Z\n" in text
    assert text.endswith("earlier thoughts\nmore\n")


def test_errors_when_no_vault_configured(
    runner: CliRunner, tmp_path: Path, freeze_clock: list[datetime]
) -> None:
    del freeze_clock
    empty_cfg = tmp_path / "empty"
    empty_cfg.mkdir()
    result = runner.invoke(
        cli,
        ["daily", "--config-dir", str(empty_cfg)],
        env={"EDITOR": "true"},
    )
    assert result.exit_code != 0
    assert "no vault configured" in result.output
