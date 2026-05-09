"""Tests for `treebeard.commands.grep` — rg+fzf invocation and dispatch."""

from __future__ import annotations

import pathlib
import subprocess
from typing import Any

import pytest
from click.testing import CliRunner

from tests.conftest import write_cfg
from treebeard import dependencies as deps_mod
from treebeard.cli import cli
from treebeard.commands import grep as grep_mod


def _patch_binaries_present(monkeypatch: pytest.MonkeyPatch, *, with_bat: bool = True) -> None:
    def which(name: str) -> str | None:
        if name == "bat" and not with_bat:
            return None
        return f"/usr/bin/{name}"

    monkeypatch.setattr(deps_mod.shutil, "which", which)


def _patch_fzf(
    monkeypatch: pytest.MonkeyPatch,
    *,
    stdout: str,
    returncode: int = 0,
    capture: list[dict[str, Any]] | None = None,
) -> None:
    """Intercept *only* fzf invocations; let other subprocess calls (git
    in the auto-commit / post-edit close hook, especially) reach the
    real implementation."""
    real_run = subprocess.run

    def fake_run(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        cmd = args[0] if args else kwargs.get("args", [])
        is_fzf = bool(cmd) and isinstance(cmd, list) and "fzf" in str(cmd[0])
        if not is_fzf:
            return real_run(*args, **kwargs)
        if capture is not None:
            capture.append({"args": args, "kwargs": kwargs})
        return subprocess.CompletedProcess(
            args=args, returncode=returncode, stdout=stdout, stderr=""
        )

    monkeypatch.setattr(grep_mod.subprocess, "run", fake_run)


def test_fails_when_rg_missing(
    runner: CliRunner,
    cfg_dir: pathlib.Path,
    vault: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    write_cfg(cfg_dir, vault)
    monkeypatch.setattr(
        deps_mod.shutil,
        "which",
        lambda name: None if name == "rg" else f"/usr/bin/{name}",
    )
    result = runner.invoke(cli, ["grep"])
    assert result.exit_code != 0
    assert "ripgrep is required" in result.output
    assert "brew install ripgrep" in result.output


def test_fails_when_fzf_missing(
    runner: CliRunner,
    cfg_dir: pathlib.Path,
    vault: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    write_cfg(cfg_dir, vault)
    monkeypatch.setattr(
        deps_mod.shutil,
        "which",
        lambda name: None if name == "fzf" else f"/usr/bin/{name}",
    )
    result = runner.invoke(cli, ["grep"])
    assert result.exit_code != 0
    assert "fzf is required" in result.output


def test_enter_opens_at_line(
    runner: CliRunner,
    cfg_dir: pathlib.Path,
    vault: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    write_cfg(cfg_dir, vault)
    target = vault / "foo.md"
    target.write_text("a\nb\nmatched\n", encoding="utf-8")
    _patch_binaries_present(monkeypatch)
    _patch_fzf(monkeypatch, stdout=f"{target}:3:1:matched\n")

    captured: list[dict[str, Any]] = []

    def fake_reopen(
        path: pathlib.Path, editor: str, *, start_line: int | None = None
    ) -> pathlib.Path:
        captured.append({"path": path, "editor": editor, "start_line": start_line})
        return path

    monkeypatch.setattr(grep_mod, "reopen", fake_reopen)

    result = runner.invoke(cli, ["grep"])
    assert result.exit_code == 0, result.output
    assert captured == [{"path": target, "editor": "vim", "start_line": 3}]


def test_relative_path_resolved_against_vault(
    runner: CliRunner,
    cfg_dir: pathlib.Path,
    vault: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """rg can emit relative paths when invoked from inside the vault.
    grep should resolve those against the configured vault dir."""
    write_cfg(cfg_dir, vault)
    target = vault / "rel.md"
    target.write_text("hit\n", encoding="utf-8")
    _patch_binaries_present(monkeypatch)
    _patch_fzf(monkeypatch, stdout="rel.md:1:1:hit\n")

    captured: list[dict[str, Any]] = []

    def fake_reopen(
        path: pathlib.Path, editor: str, *, start_line: int | None = None
    ) -> pathlib.Path:
        captured.append({"path": path, "editor": editor, "start_line": start_line})
        return path

    monkeypatch.setattr(grep_mod, "reopen", fake_reopen)

    result = runner.invoke(cli, ["grep"])
    assert result.exit_code == 0, result.output
    assert captured[0]["path"] == vault / "rel.md"


def test_esc_cancels_silently(
    runner: CliRunner,
    cfg_dir: pathlib.Path,
    vault: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    write_cfg(cfg_dir, vault)
    _patch_binaries_present(monkeypatch)
    _patch_fzf(monkeypatch, stdout="", returncode=130)

    result = runner.invoke(cli, ["grep"])
    assert result.exit_code == 0, result.output
    assert result.output == ""


def test_empty_selection_returns(
    runner: CliRunner,
    cfg_dir: pathlib.Path,
    vault: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """fzf can exit 0 with empty stdout when the user dismisses without
    a match; we should no-op rather than parse an empty string."""
    write_cfg(cfg_dir, vault)
    _patch_binaries_present(monkeypatch)
    _patch_fzf(monkeypatch, stdout="", returncode=0)

    monkeypatch.setattr(
        grep_mod,
        "reopen",
        lambda *a, **k: pytest.fail("reopen should not be called on empty selection"),
    )
    result = runner.invoke(cli, ["grep"])
    assert result.exit_code == 0, result.output


def test_preview_uses_bat_when_available(
    runner: CliRunner,
    cfg_dir: pathlib.Path,
    vault: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    write_cfg(cfg_dir, vault)
    _patch_binaries_present(monkeypatch, with_bat=True)
    capture: list[dict[str, Any]] = []
    _patch_fzf(monkeypatch, stdout="", returncode=130, capture=capture)
    runner.invoke(cli, ["grep"])
    assert capture, "fzf was not invoked"
    cmd = capture[0]["args"][0]
    preview_flag = next((a for a in cmd if a.startswith("--preview=")), None)
    assert preview_flag is not None
    assert "bat" in preview_flag
    assert "--highlight-line {2}" in preview_flag


def test_preview_falls_back_to_cat(
    runner: CliRunner,
    cfg_dir: pathlib.Path,
    vault: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    write_cfg(cfg_dir, vault)
    _patch_binaries_present(monkeypatch, with_bat=False)
    capture: list[dict[str, Any]] = []
    _patch_fzf(monkeypatch, stdout="", returncode=130, capture=capture)
    runner.invoke(cli, ["grep"])
    assert capture, "fzf was not invoked"
    cmd = capture[0]["args"][0]
    preview_flag = next((a for a in cmd if a.startswith("--preview=")), None)
    assert preview_flag is not None
    assert "cat" in preview_flag
    assert "bat" not in preview_flag


def test_rg_reload_command_shape(
    runner: CliRunner,
    cfg_dir: pathlib.Path,
    vault: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    write_cfg(cfg_dir, vault)
    _patch_binaries_present(monkeypatch)
    capture: list[dict[str, Any]] = []
    _patch_fzf(monkeypatch, stdout="", returncode=130, capture=capture)
    runner.invoke(cli, ["grep"])
    call = capture[0]
    cmd = call["args"][0]
    bind_flag = next((a for a in cmd if a.startswith("--bind=change:reload:")), None)
    assert bind_flag is not None
    assert "rg --column --line-number" in bind_flag
    assert "--type md" in bind_flag
    assert "{q}" in bind_flag
    assert "|| true" in bind_flag
    # rg searches `.` so result paths are relative; fzf is launched with
    # cwd=vault so that `.` resolves to the vault.
    assert bind_flag.rstrip().endswith(". || true")
    assert pathlib.Path(call["kwargs"]["cwd"]) == vault
    # Sanity: fzf must have --ansi and --disabled so rg drives the match.
    assert "--ansi" in cmd
    assert "--disabled" in cmd


def test_handles_text_with_colons(
    runner: CliRunner,
    cfg_dir: pathlib.Path,
    vault: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A matched line containing additional `:` (e.g. a URL) must not
    be split apart — only the first three colons are field separators."""
    write_cfg(cfg_dir, vault)
    target = vault / "links.md"
    target.write_text("see https://example.com/x:y\n", encoding="utf-8")
    _patch_binaries_present(monkeypatch)
    _patch_fzf(
        monkeypatch,
        stdout=f"{target}:1:5:see https://example.com/x:y\n",
    )

    captured: list[dict[str, Any]] = []

    def fake_reopen(
        path: pathlib.Path, editor: str, *, start_line: int | None = None
    ) -> pathlib.Path:
        captured.append({"path": path, "start_line": start_line})
        return path

    monkeypatch.setattr(grep_mod, "reopen", fake_reopen)
    result = runner.invoke(cli, ["grep"])
    assert result.exit_code == 0, result.output
    assert captured == [{"path": target, "start_line": 1}]


def test_handles_vault_path_with_spaces(
    runner: CliRunner,
    cfg_dir: pathlib.Path,
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A vault path with spaces must reach fzf as `cwd` (passed via argv,
    not the shell), so the rg reload string need not quote it."""
    spaced = tmp_path / "a vault"
    (spaced / ".treebeard").mkdir(parents=True)
    subprocess.run(["git", "init", "--quiet", "-b", "main"], cwd=spaced, check=True)
    write_cfg(cfg_dir, spaced)
    _patch_binaries_present(monkeypatch)
    capture: list[dict[str, Any]] = []
    _patch_fzf(monkeypatch, stdout="", returncode=130, capture=capture)
    runner.invoke(cli, ["grep"])
    call = capture[0]
    assert pathlib.Path(call["kwargs"]["cwd"]) == spaced
    bind_flag = next(
        (a for a in call["args"][0] if a.startswith("--bind=change:reload:")),
        None,
    )
    assert bind_flag is not None
    # The vault path must NOT appear in the shell command — fzf's cwd
    # carries it, and rg searches `.`.
    assert str(spaced) not in bind_flag


def test_missing_target_file_errors(
    runner: CliRunner,
    cfg_dir: pathlib.Path,
    vault: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Defensive: if rg reports a file that's gone by the time the user
    selects it, surface a friendly error rather than crashing in reopen."""
    write_cfg(cfg_dir, vault)
    _patch_binaries_present(monkeypatch)
    _patch_fzf(monkeypatch, stdout=f"{vault}/ghost.md:1:1:hit\n")

    result = runner.invoke(cli, ["grep"])
    assert result.exit_code != 0
    assert "no longer exists" in result.output
