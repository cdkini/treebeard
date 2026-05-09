"""Tests for `treebeard.commands.open_` — fzf invocation and dispatch."""

from __future__ import annotations

import pathlib
import subprocess
from typing import Any

import pytest
from click.testing import CliRunner

from tests.conftest import EditorFake, write_cfg
from treebeard import dependencies as deps_mod
from treebeard.cli import cli
from treebeard.commands import open_ as open_mod


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
    monkeypatch.setattr(deps_mod.shutil, "which", lambda name: f"/usr/bin/{name}")


def _patch_fzf(
    monkeypatch: pytest.MonkeyPatch,
    *,
    stdout: str,
    returncode: int = 0,
    capture: list[dict[str, Any]] | None = None,
) -> None:
    """Patch `subprocess.run` inside open_ module to return canned output
    *only* for fzf calls. Non-fzf subprocess (e.g. git in the close
    hook) passes through to the real implementation."""

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

    monkeypatch.setattr(open_mod.subprocess, "run", fake_run)


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
    result = runner.invoke(cli, ["open"])
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
    result = runner.invoke(cli, ["open"])
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

    result = runner.invoke(cli, ["open"])
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
    result = runner.invoke(cli, ["open"])
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
    result = runner.invoke(cli, ["open"])
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

    result = runner.invoke(cli, ["open"])
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

    result = runner.invoke(cli, ["open"])
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
    _patch_fzf_present(monkeypatch)
    capture: list[dict[str, Any]] = []
    _patch_fzf(monkeypatch, stdout="", returncode=130, capture=capture)
    runner.invoke(cli, ["open"])
    assert capture, "fzf was not invoked"
    cmd = capture[0]["args"][0]
    preview_flag = next((a for a in cmd if a.startswith("--preview=")), None)
    assert preview_flag is not None
    assert "bat" in preview_flag


def test_bare_om_shows_help(
    runner: CliRunner,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Bare `treebeard` should print the subcommand list, not run the picker."""
    _patch_fzf_present(monkeypatch)
    result = runner.invoke(cli, [])
    assert "Commands" in result.output
    assert "open" in result.output
    assert "grep" in result.output
    assert "chat" in result.output


def test_explicit_open_lists_all_notes(
    runner: CliRunner,
    cfg_dir: pathlib.Path,
    vault: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    fake_editor: list[EditorFake],
    freeze_now: list,
) -> None:
    """`treebeard open` (no --limit) should list every note, not just 20."""
    del freeze_now, fake_editor
    write_cfg(cfg_dir, vault)
    for i in range(25):
        _seed_note(vault, f"n{i:02d}.md", f"n{i:02d}")
    _patch_fzf_present(monkeypatch)
    capture: list[dict[str, Any]] = []
    _patch_fzf(monkeypatch, stdout="", returncode=130, capture=capture)
    runner.invoke(cli, ["open"])
    fzf_input = capture[0]["kwargs"]["input"]
    assert len(fzf_input.strip().split("\n")) == 25


def test_open_with_limit(
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
    runner.invoke(cli, ["open", "--limit", "5"])
    fzf_input = capture[0]["kwargs"]["input"]
    assert len(fzf_input.strip().split("\n")) == 5


def test_query_opens_top_match(
    runner: CliRunner,
    cfg_dir: pathlib.Path,
    vault: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    fake_editor: list[EditorFake],
    freeze_now: list,
) -> None:
    """`treebeard open foo` runs fzf in --filter mode and opens the top stem match."""
    del freeze_now, fake_editor
    write_cfg(cfg_dir, vault)
    target = _seed_note(vault, "foo.md", "foo")
    _seed_note(vault, "bar.md", "bar")
    _patch_fzf_present(monkeypatch)
    # `--filter` prints the matched stem on stdout, best-first.
    _patch_fzf(monkeypatch, stdout=f"{target.stem}\nbar\n")

    result = runner.invoke(cli, ["open", "foo"])
    assert result.exit_code == 0, result.output
    assert str(target) in result.output


def test_query_no_match_errors(
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
    # fzf exits 1 when nothing matches.
    _patch_fzf(monkeypatch, stdout="", returncode=1)

    result = runner.invoke(cli, ["open", "zzz"])
    assert result.exit_code != 0
    assert "no note matches 'zzz'" in result.output
    assert "treebeard open" in result.output  # hint points at interactive picker


def test_query_with_limit_respects_pool(
    runner: CliRunner,
    cfg_dir: pathlib.Path,
    vault: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    fake_editor: list[EditorFake],
    freeze_now: list,
) -> None:
    """`--limit N` caps the candidate pool that the query filters within."""
    del freeze_now, fake_editor
    write_cfg(cfg_dir, vault)
    for i in range(5):
        _seed_note(vault, f"n{i}.md", f"n{i}")
    _patch_fzf_present(monkeypatch)
    capture: list[dict[str, Any]] = []
    # fzf will be handed only 2 stems; the canned stdout names one of them.
    _patch_fzf(monkeypatch, stdout="n4\n", capture=capture)

    result = runner.invoke(cli, ["open", "n", "--limit", "2"])
    assert result.exit_code == 0, result.output
    fzf_input = capture[0]["kwargs"]["input"]
    assert len(fzf_input.strip().split("\n")) == 2


def test_query_uses_filter_mode(
    runner: CliRunner,
    cfg_dir: pathlib.Path,
    vault: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    fake_editor: list[EditorFake],
    freeze_now: list,
) -> None:
    """Query path uses non-interactive flags only — no preview, no expect."""
    del freeze_now, fake_editor
    write_cfg(cfg_dir, vault)
    _seed_note(vault, "foo.md", "foo")
    _patch_fzf_present(monkeypatch)
    capture: list[dict[str, Any]] = []
    _patch_fzf(monkeypatch, stdout="foo\n", capture=capture)

    runner.invoke(cli, ["open", "foo"])
    argv = capture[0]["args"][0]
    assert any(a.startswith("--filter=") for a in argv)
    assert not any(a.startswith("--preview=") for a in argv)
    assert "--expect=ctrl-n" not in argv
    assert "--print-query" not in argv


def test_query_with_spaces(
    runner: CliRunner,
    cfg_dir: pathlib.Path,
    vault: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    fake_editor: list[EditorFake],
    freeze_now: list,
) -> None:
    """`treebeard open foo bar` (no quotes) joins to a single fzf query."""
    del freeze_now, fake_editor
    write_cfg(cfg_dir, vault)
    target = _seed_note(vault, "foo-bar.md", "foo bar")
    _patch_fzf_present(monkeypatch)
    capture: list[dict[str, Any]] = []
    _patch_fzf(monkeypatch, stdout=f"{target.stem}\n", capture=capture)

    result = runner.invoke(cli, ["open", "foo", "bar"])
    assert result.exit_code == 0, result.output
    argv = capture[0]["args"][0]
    assert "--filter=foo bar" in argv


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
        if name in {"fzf", "rg", "git", "vim"}:
            return f"/usr/bin/{name}"
        return None

    monkeypatch.setattr(deps_mod.shutil, "which", which)
    capture: list[dict[str, Any]] = []
    _patch_fzf(monkeypatch, stdout="", returncode=130, capture=capture)
    runner.invoke(cli, ["open"])
    assert capture, "fzf was not invoked"
    cmd = capture[0]["args"][0]
    preview_flag = next((a for a in cmd if a.startswith("--preview=")), None)
    assert preview_flag is not None
    assert "cat" in preview_flag
    assert "bat" not in preview_flag
