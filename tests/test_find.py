"""Tests for `om.commands.find` — fzf invocation and dispatch."""

from __future__ import annotations

import pathlib
import subprocess
from typing import Any

import pytest
from click.testing import CliRunner

from om.cli import cli
from om.commands import find as find_mod
from tests.conftest import EditorFake, write_cfg


def _seed_note(vault: pathlib.Path, name: str, title: str, body: str = "body\n") -> pathlib.Path:
    path = vault / name
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
    return path


def _patch_fzf_present(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(find_mod.shutil, "which", lambda name: f"/usr/bin/{name}")


def _patch_fzf(
    monkeypatch: pytest.MonkeyPatch,
    *,
    stdout: str,
    returncode: int = 0,
    capture: list[dict[str, Any]] | None = None,
) -> None:
    """Patch `subprocess.run` inside find module to return canned output."""

    def fake_run(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        if capture is not None:
            capture.append({"args": args, "kwargs": kwargs})
        return subprocess.CompletedProcess(
            args=args, returncode=returncode, stdout=stdout, stderr=""
        )

    monkeypatch.setattr(find_mod.subprocess, "run", fake_run)


def test_fails_when_fzf_missing(
    runner: CliRunner,
    cfg_dir: pathlib.Path,
    vault: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    write_cfg(cfg_dir, vault)
    monkeypatch.setattr(find_mod.shutil, "which", lambda name: None)
    result = runner.invoke(cli, ["find", "--config-dir", str(cfg_dir)])
    assert result.exit_code != 0
    assert "fzf is required" in result.output
    assert "brew install fzf" in result.output


def test_empty_vault(
    runner: CliRunner,
    cfg_dir: pathlib.Path,
    vault: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    write_cfg(cfg_dir, vault)
    _patch_fzf_present(monkeypatch)
    result = runner.invoke(cli, ["find", "--config-dir", str(cfg_dir)])
    assert result.exit_code == 0, result.output
    assert "vault is empty" in result.output


def test_enter_opens_selected(
    runner: CliRunner,
    cfg_dir: pathlib.Path,
    vault: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    fake_editor: list[EditorFake],
    freeze_now: list,
) -> None:
    del freeze_now, fake_editor
    write_cfg(cfg_dir, vault)
    target = _seed_note(vault, "foo.md", "foo")
    _patch_fzf_present(monkeypatch)
    # query (empty), key (empty = Enter), selection
    fzf_stdout = f"\n\nfoo  (foo)  just now\t{target}\n"
    _patch_fzf(monkeypatch, stdout=fzf_stdout)

    result = runner.invoke(cli, ["find", "--config-dir", str(cfg_dir)])
    assert result.exit_code == 0, result.output
    assert str(target) in result.output


def test_ctrl_n_with_query_creates_named(
    runner: CliRunner,
    cfg_dir: pathlib.Path,
    vault: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    fake_editor: list[EditorFake],
    freeze_now: list,
) -> None:
    del freeze_now
    write_cfg(cfg_dir, vault)
    _seed_note(vault, "existing.md", "existing")  # something to list
    _patch_fzf_present(monkeypatch)
    # query, key=ctrl-n, no selection
    _patch_fzf(monkeypatch, stdout="my new idea\nctrl-n\n\n")

    def add_body(_ed: str, p: pathlib.Path) -> None:
        p.write_text(p.read_text(encoding="utf-8") + "stuff\n", encoding="utf-8")

    fake_editor.append(add_body)
    result = runner.invoke(cli, ["find", "--config-dir", str(cfg_dir)])
    assert result.exit_code == 0, result.output
    assert (vault / "my-new-idea.md").exists()


def test_ctrl_n_empty_query_creates_scratch(
    runner: CliRunner,
    cfg_dir: pathlib.Path,
    vault: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    fake_editor: list[EditorFake],
    freeze_now: list,
) -> None:
    del freeze_now
    write_cfg(cfg_dir, vault)
    _seed_note(vault, "existing.md", "existing")
    _patch_fzf_present(monkeypatch)
    _patch_fzf(monkeypatch, stdout="\nctrl-n\n\n")

    def add_body(_ed: str, p: pathlib.Path) -> None:
        p.write_text(p.read_text(encoding="utf-8") + "stuff\n", encoding="utf-8")

    fake_editor.append(add_body)
    result = runner.invoke(cli, ["find", "--config-dir", str(cfg_dir)])
    assert result.exit_code == 0, result.output
    assert (vault / "scratch-2026-05-07t14-23-05.md").exists()


def test_ctrl_n_query_collides_errors(
    runner: CliRunner,
    cfg_dir: pathlib.Path,
    vault: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    fake_editor: list[EditorFake],
    freeze_now: list,
) -> None:
    del freeze_now, fake_editor
    write_cfg(cfg_dir, vault)
    _seed_note(vault, "foo.md", "foo")
    _patch_fzf_present(monkeypatch)
    _patch_fzf(monkeypatch, stdout="foo\nctrl-n\n\n")

    result = runner.invoke(cli, ["find", "--config-dir", str(cfg_dir)])
    assert result.exit_code != 0
    assert "already exists" in result.output


def test_esc_cancels_silently(
    runner: CliRunner,
    cfg_dir: pathlib.Path,
    vault: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    fake_editor: list[EditorFake],
    freeze_now: list,
) -> None:
    del freeze_now, fake_editor
    write_cfg(cfg_dir, vault)
    _seed_note(vault, "foo.md", "foo")
    _patch_fzf_present(monkeypatch)
    _patch_fzf(monkeypatch, stdout="", returncode=130)

    result = runner.invoke(cli, ["find", "--config-dir", str(cfg_dir)])
    assert result.exit_code == 0, result.output


def test_preview_uses_configured_previewer(
    runner: CliRunner,
    cfg_dir: pathlib.Path,
    vault: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    fake_editor: list[EditorFake],
    freeze_now: list,
) -> None:
    del freeze_now, fake_editor
    write_cfg(cfg_dir, vault, previewer="bat")
    _seed_note(vault, "foo.md", "foo")
    monkeypatch.setattr(find_mod.shutil, "which", lambda name: f"/usr/bin/{name}")
    capture: list[dict[str, Any]] = []
    _patch_fzf(monkeypatch, stdout="", returncode=130, capture=capture)
    runner.invoke(cli, ["find", "--config-dir", str(cfg_dir)])
    assert capture, "fzf was not invoked"
    cmd = capture[0]["args"][0]
    preview_flag = next((a for a in cmd if a.startswith("--preview=")), None)
    assert preview_flag is not None
    assert "bat" in preview_flag


def test_bare_om_invokes_find(
    runner: CliRunner,
    cfg_dir: pathlib.Path,
    vault: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    fake_editor: list[EditorFake],
    freeze_now: list,
) -> None:
    """`om` with no subcommand should route through find."""
    del freeze_now, fake_editor
    write_cfg(cfg_dir, vault)
    target = _seed_note(vault, "foo.md", "foo")
    _patch_fzf_present(monkeypatch)
    _patch_fzf(monkeypatch, stdout=f"\n\nfoo  (foo)  just now\t{target}\n")

    # The bare-om dispatch path uses the default config dir; we override
    # via env var since there's no flag pre-subcommand. Easiest: just
    # invoke via the explicit find path under test, plus a separate
    # smoke that exercises the default-no-subcommand wiring.
    monkeypatch.setattr(
        "om.commands.find.load_config",
        lambda _cd: __import__("om.config", fromlist=["Config"]).Config(vault=vault, editor="vim"),
    )
    result = runner.invoke(cli, [])
    assert result.exit_code == 0, result.output
    assert str(target) in result.output


def test_explicit_find_lists_all_notes(
    runner: CliRunner,
    cfg_dir: pathlib.Path,
    vault: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    fake_editor: list[EditorFake],
    freeze_now: list,
) -> None:
    """`om find` (no --limit) should list every note, not just 20."""
    del freeze_now, fake_editor
    write_cfg(cfg_dir, vault)
    for i in range(25):
        _seed_note(vault, f"n{i:02d}.md", f"n{i:02d}")
    _patch_fzf_present(monkeypatch)
    capture: list[dict[str, Any]] = []
    _patch_fzf(monkeypatch, stdout="", returncode=130, capture=capture)
    runner.invoke(cli, ["find", "--config-dir", str(cfg_dir)])
    fzf_input = capture[0]["kwargs"]["input"]
    assert len(fzf_input.strip().split("\n")) == 25


def test_find_with_limit(
    runner: CliRunner,
    cfg_dir: pathlib.Path,
    vault: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    fake_editor: list[EditorFake],
    freeze_now: list,
) -> None:
    del freeze_now, fake_editor
    write_cfg(cfg_dir, vault)
    for i in range(15):
        _seed_note(vault, f"n{i:02d}.md", f"n{i:02d}")
    _patch_fzf_present(monkeypatch)
    capture: list[dict[str, Any]] = []
    _patch_fzf(monkeypatch, stdout="", returncode=130, capture=capture)
    runner.invoke(cli, ["find", "--config-dir", str(cfg_dir), "--limit", "5"])
    fzf_input = capture[0]["kwargs"]["input"]
    assert len(fzf_input.strip().split("\n")) == 5


def test_preview_falls_back_to_cat(
    runner: CliRunner,
    cfg_dir: pathlib.Path,
    vault: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    fake_editor: list[EditorFake],
    freeze_now: list,
) -> None:
    del freeze_now, fake_editor
    write_cfg(cfg_dir, vault)
    _seed_note(vault, "foo.md", "foo")

    def which(name: str) -> str | None:
        return "/usr/bin/fzf" if name == "fzf" else None

    monkeypatch.setattr(find_mod.shutil, "which", which)
    capture: list[dict[str, Any]] = []
    _patch_fzf(monkeypatch, stdout="", returncode=130, capture=capture)
    runner.invoke(cli, ["find", "--config-dir", str(cfg_dir)])
    assert capture, "fzf was not invoked"
    cmd = capture[0]["args"][0]
    preview_flag = next((a for a in cmd if a.startswith("--preview=")), None)
    assert preview_flag is not None
    assert "cat" in preview_flag
    assert "bat" not in preview_flag
