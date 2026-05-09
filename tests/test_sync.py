"""Tests for `treebeard sync`."""

from __future__ import annotations

import pathlib
import subprocess

from click.testing import CliRunner

from tests.conftest import write_cfg
from treebeard.cli import cli


def _git(vault: pathlib.Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=vault,
        check=True,
        capture_output=True,
        text=True,
    ).stdout


def _commit_baseline(vault: pathlib.Path) -> None:
    (vault / "README.md").write_text("hello\n", encoding="utf-8")
    _git(vault, "add", "-A")
    _git(vault, "commit", "--quiet", "-m", "baseline")


def _bare_remote(tmp_path: pathlib.Path) -> pathlib.Path:
    bare = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", "--quiet", str(bare)], check=True)
    return bare


def test_errors_with_no_remote(
    runner: CliRunner, cfg_dir: pathlib.Path, vault: pathlib.Path
) -> None:
    write_cfg(cfg_dir, vault)

    result = runner.invoke(cli, ["sync"])

    assert result.exit_code != 0
    assert "no git remote configured" in result.output


def test_pulls_and_pushes(
    runner: CliRunner,
    cfg_dir: pathlib.Path,
    vault: pathlib.Path,
    tmp_path: pathlib.Path,
) -> None:
    bare = _bare_remote(tmp_path)
    _commit_baseline(vault)
    _git(vault, "remote", "add", "origin", str(bare))
    _git(vault, "push", "--quiet", "-u", "origin", "HEAD:main")

    # Make a local change so sync has something to push.
    (vault / "new.md").write_text("new\n", encoding="utf-8")
    _git(vault, "add", "-A")
    _git(vault, "commit", "--quiet", "-m", "local change")

    write_cfg(cfg_dir, vault)
    result = runner.invoke(cli, ["sync"])

    assert result.exit_code == 0, result.output
    assert "Synced." in result.output

    log = subprocess.run(
        ["git", "--git-dir", str(bare), "log", "--format=%s", "main"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    assert "local change" in log


def test_errors_when_remote_unreachable(
    runner: CliRunner,
    cfg_dir: pathlib.Path,
    vault: pathlib.Path,
    tmp_path: pathlib.Path,
) -> None:
    _commit_baseline(vault)
    _git(vault, "remote", "add", "origin", str(tmp_path / "does-not-exist.git"))

    write_cfg(cfg_dir, vault)
    result = runner.invoke(cli, ["sync"])

    assert result.exit_code != 0
    assert "sync failed" in result.output


def test_errors_when_vault_is_missing_git(
    runner: CliRunner, cfg_dir: pathlib.Path, tmp_path: pathlib.Path
) -> None:
    half = tmp_path / "half"
    (half / ".treebeard").mkdir(parents=True)
    write_cfg(cfg_dir, half)

    result = runner.invoke(cli, ["sync"])

    assert result.exit_code != 0
    assert "missing .git/" in result.output
