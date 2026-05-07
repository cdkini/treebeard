"""Tests for the CLI-level auto-commit hook in `om.cli`."""

from __future__ import annotations

import pathlib
import re
import subprocess
from datetime import UTC, datetime

import pytest
from click.testing import CliRunner

from om import cli as cli_mod
from om.cli import cli
from tests.conftest import EditorFake, write_cfg


def _git(vault: pathlib.Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=vault,
        check=True,
        capture_output=True,
        text=True,
    ).stdout


def _head_message(vault: pathlib.Path) -> str:
    return _git(vault, "log", "-1", "--format=%s").strip()


def _commit_count(vault: pathlib.Path) -> int:
    out = _git(vault, "rev-list", "--count", "--all").strip()
    return int(out) if out else 0


def _append(payload: str) -> EditorFake:
    def _do(_ed: str, p: pathlib.Path) -> None:
        p.write_text(p.read_text(encoding="utf-8") + payload, encoding="utf-8")

    return _do


def test_commits_when_dirty(
    runner: CliRunner,
    cfg_dir: pathlib.Path,
    vault: pathlib.Path,
    fake_editor: list[EditorFake],
    freeze_now: list,
) -> None:
    del freeze_now
    fake_editor.append(_append("body\n"))
    write_cfg(cfg_dir, vault)

    before = _commit_count(vault)
    result = runner.invoke(cli, ["note", "--config-dir", str(cfg_dir), "hello"])
    assert result.exit_code == 0, result.output

    after = _commit_count(vault)
    assert after == before + 1
    assert re.match(r"^note: \d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$", _head_message(vault))


def test_uses_subcommand_name_in_message(
    runner: CliRunner,
    cfg_dir: pathlib.Path,
    vault: pathlib.Path,
    fake_editor: list[EditorFake],
    freeze_now: list,
    freeze_today: None,
) -> None:
    del freeze_now, freeze_today
    fake_editor.append(_append("hi\n"))
    write_cfg(cfg_dir, vault)

    result = runner.invoke(cli, ["daily", "--config-dir", str(cfg_dir)])
    assert result.exit_code == 0, result.output
    assert _head_message(vault).startswith("daily: ")


def test_noop_when_clean(
    runner: CliRunner,
    cfg_dir: pathlib.Path,
    vault: pathlib.Path,
) -> None:
    write_cfg(cfg_dir, vault)
    # Establish a baseline commit so the tree is clean.
    (vault / "README.md").write_text("hello\n", encoding="utf-8")
    _git(vault, "add", "-A")
    _git(vault, "commit", "--quiet", "-m", "baseline")

    before = _commit_count(vault)
    result = runner.invoke(cli, ["note", "--help"])
    assert result.exit_code == 0, result.output
    assert _commit_count(vault) == before


def test_silent_when_no_git(
    runner: CliRunner,
    cfg_dir: pathlib.Path,
    tmp_path: pathlib.Path,
) -> None:
    """If a vault somehow lacks .git/ (e.g. a partial init), the hook
    should swallow the error rather than crash the user's command."""
    half = tmp_path / "half"
    (half / ".om").mkdir(parents=True)
    write_cfg(cfg_dir, half)

    # `om note --help` doesn't load the config so it won't error on the
    # missing .git/ — but the hook will run on close. It must not raise.
    result = runner.invoke(cli, ["note", "--help"])
    assert result.exit_code == 0, result.output


def test_working_tree_clean_after_successful_command(
    runner: CliRunner,
    cfg_dir: pathlib.Path,
    vault: pathlib.Path,
    fake_editor: list[EditorFake],
    freeze_now: list,
) -> None:
    """After a successful command, nothing should remain unstaged."""
    del freeze_now
    fake_editor.append(_append("body\n"))
    write_cfg(cfg_dir, vault)

    result = runner.invoke(cli, ["note", "--config-dir", str(cfg_dir), "hello"])
    assert result.exit_code == 0, result.output

    status = _git(vault, "status", "--porcelain").strip()
    assert status == "", f"unexpected dirty tree: {status!r}"


def test_timestamp_is_utc_iso_z(
    runner: CliRunner,
    cfg_dir: pathlib.Path,
    vault: pathlib.Path,
    fake_editor: list[EditorFake],
    freeze_now: list,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del freeze_now
    fake_editor.append(_append("body\n"))
    write_cfg(cfg_dir, vault)

    frozen = datetime(2026, 5, 7, 14, 23, 5, tzinfo=UTC)

    class _FrozenDatetime:
        @staticmethod
        def now(tz=None):
            return frozen

    monkeypatch.setattr(cli_mod, "datetime", _FrozenDatetime)

    result = runner.invoke(cli, ["note", "--config-dir", str(cfg_dir), "hello"])
    assert result.exit_code == 0, result.output
    assert _head_message(vault) == "note: 2026-05-07T14:23:05Z"
