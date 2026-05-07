"""Tests for `om daily`."""

from __future__ import annotations

import os
import pathlib
import stat
from datetime import UTC, date, datetime

import pytest
from click.testing import CliRunner

from om import editor as editor_mod
from om.cli import cli
from om.commands import daily as daily_cmd
from om.commands import note as note_cmd
from om.config import Config

FROZEN_NOW = datetime(2026, 5, 7, 14, 23, 5, tzinfo=UTC)
FROZEN_LATER = datetime(2026, 5, 7, 15, 0, 0, tzinfo=UTC)
FROZEN_TODAY = date(2026, 5, 7)
FROZEN_FRONTMATTER = (
    "---\n"
    "title: 2026-05-07\n"
    "source: user\n"
    "created_at: 2026-05-07T14:23:05Z\n"
    "updated_at: 2026-05-07T14:23:05Z\n"
    "tags: [daily]\n"
    "---\n"
)
EMPTY_BODY = "\n### Notes\n\n\n### TODOs\n\n\n"


@pytest.fixture
def vault(tmp_path: pathlib.Path) -> pathlib.Path:
    v = tmp_path / "vault"
    (v / ".om").mkdir(parents=True)
    return v


@pytest.fixture
def cfg_dir(tmp_path: pathlib.Path) -> pathlib.Path:
    d = tmp_path / "cfg"
    d.mkdir()
    return d


def _write_cfg(cfg_dir: pathlib.Path, vault: pathlib.Path, editor: str) -> None:
    Config(vault=vault, editor=editor).save(str(cfg_dir))


@pytest.fixture
def freeze_clock(monkeypatch: pytest.MonkeyPatch) -> list[datetime]:
    """Freeze the clocks used across `note`, `daily`, and `editor`."""
    queue: list[datetime] = [FROZEN_NOW]

    def fake_now() -> datetime:
        return queue.pop(0) if len(queue) > 1 else queue[0]

    monkeypatch.setattr(note_cmd, "_now_utc", fake_now)
    monkeypatch.setattr(editor_mod, "_now_utc", fake_now)
    monkeypatch.setattr(daily_cmd, "_today_local", lambda: FROZEN_TODAY)
    return queue


def _make_script(tmp_path: pathlib.Path, body: str, name: str = "edit.sh") -> pathlib.Path:
    script = tmp_path / name
    # Skip any leading `+...` arg so the script can stand in for vim/nvim,
    # which `om` invokes as `vim + <path>` to land the cursor at EOF.
    preamble = 'while [ "${1#+}" != "$1" ]; do shift; done\n'
    script.write_text(f"#!/bin/sh\n{preamble}{body}\n", encoding="utf-8")
    script.chmod(script.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return script


def _appender(
    tmp_path: pathlib.Path, payload: str = "edited\n", name: str = "appender.sh"
) -> pathlib.Path:
    return _make_script(tmp_path, f'printf "%s" "{payload}" >> "$1"', name=name)


def test_help(runner: CliRunner) -> None:
    result = runner.invoke(cli, ["daily", "--help"])
    assert result.exit_code == 0, result.output
    assert "daily" in result.output


def test_creates_todays_file_with_daily_tag(
    runner: CliRunner,
    cfg_dir: pathlib.Path,
    vault: pathlib.Path,
    tmp_path: pathlib.Path,
    freeze_clock: list[datetime],
) -> None:
    del freeze_clock
    script = _appender(tmp_path)
    _write_cfg(cfg_dir, vault, str(script))
    result = runner.invoke(cli, ["daily", "--config-dir", str(cfg_dir)])
    assert result.exit_code == 0, result.output
    path = vault / "2026-05-07.md"
    assert path.exists()
    text = path.read_text(encoding="utf-8")
    assert text == FROZEN_FRONTMATTER + EMPTY_BODY + "edited\n"
    assert str(path) in result.output


def test_discards_when_unchanged(
    runner: CliRunner, cfg_dir: pathlib.Path, vault: pathlib.Path, freeze_clock: list[datetime]
) -> None:
    del freeze_clock
    _write_cfg(cfg_dir, vault, "true")
    result = runner.invoke(cli, ["daily", "--config-dir", str(cfg_dir)])
    assert result.exit_code == 0, result.output
    assert not (vault / "2026-05-07.md").exists()
    assert "discarded empty note" in result.output


def test_reopens_existing_daily_note_preserving_tags(
    runner: CliRunner,
    cfg_dir: pathlib.Path,
    vault: pathlib.Path,
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(daily_cmd, "_today_local", lambda: FROZEN_TODAY)
    monkeypatch.setattr(note_cmd, "_now_utc", lambda: FROZEN_LATER)
    monkeypatch.setattr(editor_mod, "_now_utc", lambda: FROZEN_LATER)

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
    _write_cfg(cfg_dir, vault, str(script))
    result = runner.invoke(cli, ["daily", "--config-dir", str(cfg_dir)])
    assert result.exit_code == 0, result.output
    text = path.read_text(encoding="utf-8")
    assert "tags: [daily, journal]\n" in text
    assert "created_at: 2026-05-07T10:00:00Z\n" in text
    assert "updated_at: 2026-05-07T15:00:00Z\n" in text
    assert text.endswith("earlier thoughts\nmore\n")


def test_errors_when_no_vault_configured(
    runner: CliRunner, tmp_path: pathlib.Path, freeze_clock: list[datetime]
) -> None:
    del freeze_clock
    empty_cfg = tmp_path / "empty"
    empty_cfg.mkdir()
    result = runner.invoke(cli, ["daily", "--config-dir", str(empty_cfg)])
    assert result.exit_code != 0
    assert "no vault configured" in result.output


def _write_prior_daily(vault: pathlib.Path, name: str, body: str) -> None:
    """Write a prior daily with valid frontmatter so `split_document` works."""
    iso = name[:-3] if name.endswith(".md") else name
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


def test_carries_forward_flat_open_todos(
    runner: CliRunner,
    cfg_dir: pathlib.Path,
    vault: pathlib.Path,
    tmp_path: pathlib.Path,
    freeze_clock: list[datetime],
) -> None:
    del freeze_clock
    _write_prior_daily(
        vault,
        "2026-05-06",
        "- [ ] write the design doc\n- [x] reply to Carl\n- [ ] schedule retro\n",
    )
    script = _appender(tmp_path, payload="")
    _write_cfg(cfg_dir, vault, str(script))
    result = runner.invoke(cli, ["daily", "--config-dir", str(cfg_dir)])
    assert result.exit_code == 0, result.output
    text = (vault / "2026-05-07.md").read_text(encoding="utf-8")
    expected_todos = (
        "### TODOs\n"
        "- [ ] write the design doc (from 05/06)\n"
        "- [ ] schedule retro (from 05/06)\n"
        "\n\n"
    )
    assert expected_todos in text
    assert "reply to Carl" not in text


def test_completed_parent_drops_entire_subtree(
    runner: CliRunner,
    cfg_dir: pathlib.Path,
    vault: pathlib.Path,
    freeze_clock: list[datetime],
) -> None:
    del freeze_clock
    _write_prior_daily(
        vault,
        "2026-05-06",
        "- [x] Send weekly update\n    - [ ] follow up with Alice\n    - drafted Monday\n",
    )
    # Editor stub doesn't edit; with no carryover (everything dropped),
    # the file is discarded just like the no-prior-daily case.
    _write_cfg(cfg_dir, vault, "true")
    result = runner.invoke(cli, ["daily", "--config-dir", str(cfg_dir)])
    assert result.exit_code == 0, result.output
    assert not (vault / "2026-05-07.md").exists()
    assert "discarded empty note" in result.output


def test_open_parent_carries_subtree_minus_completed_children(
    runner: CliRunner,
    cfg_dir: pathlib.Path,
    vault: pathlib.Path,
    tmp_path: pathlib.Path,
    freeze_clock: list[datetime],
) -> None:
    del freeze_clock
    _write_prior_daily(
        vault,
        "2026-05-06",
        "- [ ] Ship migration\n"
        "    - needs SRE signoff\n"
        "    - [x] write rollback script\n"
        "    - [ ] update runbook\n",
    )
    script = _appender(tmp_path, payload="")
    _write_cfg(cfg_dir, vault, str(script))
    result = runner.invoke(cli, ["daily", "--config-dir", str(cfg_dir)])
    assert result.exit_code == 0, result.output
    text = (vault / "2026-05-07.md").read_text(encoding="utf-8")
    expected_todos = (
        "### TODOs\n"
        "- [ ] Ship migration (from 05/06)\n"
        "    - needs SRE signoff\n"
        "    - [ ] update runbook\n"
        "\n\n"
    )
    assert expected_todos in text
    assert "write rollback script" not in text


def test_carry_forward_preserves_existing_from_suffix_on_top_level(
    runner: CliRunner,
    cfg_dir: pathlib.Path,
    vault: pathlib.Path,
    tmp_path: pathlib.Path,
    freeze_clock: list[datetime],
) -> None:
    del freeze_clock
    _write_prior_daily(
        vault,
        "2026-05-06",
        "- [ ] foo (from 05/04)\n",
    )
    script = _appender(tmp_path, payload="")
    _write_cfg(cfg_dir, vault, str(script))
    result = runner.invoke(cli, ["daily", "--config-dir", str(cfg_dir)])
    assert result.exit_code == 0, result.output
    text = (vault / "2026-05-07.md").read_text(encoding="utf-8")
    assert "- [ ] foo (from 05/04)\n" in text
    assert "(from 05/06)" not in text


def test_no_prior_daily_means_empty_todos_section(
    runner: CliRunner,
    cfg_dir: pathlib.Path,
    vault: pathlib.Path,
    tmp_path: pathlib.Path,
    freeze_clock: list[datetime],
) -> None:
    del freeze_clock
    script = _appender(tmp_path, payload="")
    _write_cfg(cfg_dir, vault, str(script))
    result = runner.invoke(cli, ["daily", "--config-dir", str(cfg_dir)])
    assert result.exit_code == 0, result.output
    # Nothing was written by the editor stub; with no carryover, it gets discarded.
    assert "discarded empty note" in result.output


def test_picks_most_recent_prior_daily(
    runner: CliRunner,
    cfg_dir: pathlib.Path,
    vault: pathlib.Path,
    tmp_path: pathlib.Path,
    freeze_clock: list[datetime],
) -> None:
    del freeze_clock
    _write_prior_daily(vault, "2026-04-01", "- [ ] OLD task\n")
    _write_prior_daily(vault, "2026-05-06", "- [ ] NEW task\n")
    script = _appender(tmp_path, payload="")
    _write_cfg(cfg_dir, vault, str(script))
    result = runner.invoke(cli, ["daily", "--config-dir", str(cfg_dir)])
    assert result.exit_code == 0, result.output
    text = (vault / "2026-05-07.md").read_text(encoding="utf-8")
    assert "NEW task (from 05/06)" in text
    assert "OLD task" not in text


def test_ignores_non_daily_markdown_files_when_finding_prior(
    runner: CliRunner,
    cfg_dir: pathlib.Path,
    vault: pathlib.Path,
    tmp_path: pathlib.Path,
    freeze_clock: list[datetime],
) -> None:
    del freeze_clock
    (vault / "notes.md").write_text(
        "---\ntitle: notes\nsource: user\ncreated_at: 2026-05-06T10:00:00Z\n"
        "updated_at: 2026-05-06T10:00:00Z\ntags: []\n---\n"
        "- [ ] from notes file\n",
        encoding="utf-8",
    )
    _write_prior_daily(vault, "2026-05-06", "- [ ] from dated file\n")
    script = _appender(tmp_path, payload="")
    _write_cfg(cfg_dir, vault, str(script))
    result = runner.invoke(cli, ["daily", "--config-dir", str(cfg_dir)])
    assert result.exit_code == 0, result.output
    text = (vault / "2026-05-07.md").read_text(encoding="utf-8")
    assert "from dated file (from 05/06)" in text
    assert "from notes file" not in text


def test_ignores_invalid_date_filenames(
    runner: CliRunner,
    cfg_dir: pathlib.Path,
    vault: pathlib.Path,
    tmp_path: pathlib.Path,
    freeze_clock: list[datetime],
) -> None:
    del freeze_clock
    # Date-shaped name but invalid month/day.
    (vault / "2026-13-99.md").write_text(
        "---\ntitle: 2026-13-99\nsource: user\ncreated_at: 2026-05-06T10:00:00Z\n"
        "updated_at: 2026-05-06T10:00:00Z\ntags: []\n---\n"
        "- [ ] should not carry\n",
        encoding="utf-8",
    )
    script = _appender(tmp_path, payload="")
    _write_cfg(cfg_dir, vault, str(script))
    result = runner.invoke(cli, ["daily", "--config-dir", str(cfg_dir)])
    assert result.exit_code == 0, result.output
    # No prior daily found → empty scaffolding → discarded since editor didn't edit.
    assert "discarded empty note" in result.output


def test_does_not_recarry_when_today_already_exists(
    runner: CliRunner,
    cfg_dir: pathlib.Path,
    vault: pathlib.Path,
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reopening today's file must not inject yesterday's TODOs again."""
    monkeypatch.setattr(daily_cmd, "_today_local", lambda: FROZEN_TODAY)
    queue: list[datetime] = [FROZEN_NOW, FROZEN_LATER]

    def fake_now() -> datetime:
        return queue.pop(0) if len(queue) > 1 else queue[0]

    monkeypatch.setattr(note_cmd, "_now_utc", fake_now)
    monkeypatch.setattr(editor_mod, "_now_utc", fake_now)

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
        "\n### Notes\n\nalready noted\n\n### TODOs\n- [ ] today's task\n\n",
        encoding="utf-8",
    )
    old = today_path.stat().st_mtime - 60
    os.utime(today_path, (old, old))

    script = _appender(tmp_path, payload="")
    _write_cfg(cfg_dir, vault, str(script))
    result = runner.invoke(cli, ["daily", "--config-dir", str(cfg_dir)])
    assert result.exit_code == 0, result.output
    text = today_path.read_text(encoding="utf-8")
    assert "yesterday's open task" not in text
    assert "today's task" in text


def test_carry_forward_with_no_user_edits_does_not_discard(
    runner: CliRunner,
    cfg_dir: pathlib.Path,
    vault: pathlib.Path,
    tmp_path: pathlib.Path,
    freeze_clock: list[datetime],
) -> None:
    """When carryover is non-empty, an unedited save still keeps the file."""
    del freeze_clock
    _write_prior_daily(vault, "2026-05-06", "- [ ] survives\n")
    # Editor stub that doesn't touch the file.
    _write_cfg(cfg_dir, vault, "true")
    result = runner.invoke(cli, ["daily", "--config-dir", str(cfg_dir)])
    assert result.exit_code == 0, result.output
    path = vault / "2026-05-07.md"
    assert path.exists()
    text = path.read_text(encoding="utf-8")
    assert "- [ ] survives (from 05/06)" in text
    assert "discarded empty note" not in result.output
