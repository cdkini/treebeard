"""Tests for `treebeard.commands.archive` — fzf-driven soft-delete to .treebeard/archive/."""

from __future__ import annotations

import pathlib
import re
import subprocess
from datetime import UTC, datetime
from typing import Any

import pytest
from click.testing import CliRunner

from tests.conftest import write_cfg
from treebeard import archiver as archiver_mod
from treebeard import dependencies as deps_mod
from treebeard.cli import cli
from treebeard.commands import archive as archive_mod
from treebeard.indexer import build_indexes
from treebeard.vault import list_recent_notes


def _seed_note(
    vault: pathlib.Path,
    name: str,
    title: str,
    body: str = "body\n",
    tags: list[str] | None = None,
) -> pathlib.Path:
    path = vault / name
    tag_list = ", ".join(tags or [])
    path.write_text(
        "---\n"
        f"title: {title}\n"
        "source: user\n"
        "created_at: 2026-05-07T14:23:05Z\n"
        "updated_at: 2026-05-07T14:23:05Z\n"
        f"tags: [{tag_list}]\n"
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
    """Intercept *only* fzf invocations; let other subprocess calls (git
    via the auto-commit hook, etc.) run normally. Patching the shared
    `subprocess` module attribute would also catch git, which we want to
    stay real so commit assertions land on actual commits."""
    real_run = subprocess.run

    def fake_run(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        argv = args[0] if args else kwargs.get("args")
        is_fzf = bool(argv) and argv[0] == "fzf"
        if not is_fzf:
            return real_run(*args, **kwargs)
        if capture is not None:
            capture.append({"args": args, "kwargs": kwargs})
        return subprocess.CompletedProcess(
            args=args, returncode=returncode, stdout=stdout, stderr=""
        )

    monkeypatch.setattr(archive_mod.subprocess, "run", fake_run)


def _freeze_archive_clock(monkeypatch: pytest.MonkeyPatch, value: datetime) -> None:
    monkeypatch.setattr(archiver_mod, "_now_utc", lambda: value)


def _git_log_subject(vault: pathlib.Path) -> str:
    return subprocess.run(
        ["git", "log", "-1", "--format=%s"],
        cwd=vault,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def test_empty_vault_skips_picker(
    runner: CliRunner,
    cfg_dir: pathlib.Path,
    vault: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No notes → info message, no fzf invocation."""
    write_cfg(cfg_dir, vault)
    _patch_fzf_present(monkeypatch)
    capture: list[dict[str, Any]] = []
    _patch_fzf(monkeypatch, stdout="", capture=capture)

    result = runner.invoke(cli, ["archive"])
    assert result.exit_code == 0, result.output
    assert "nothing to archive" in result.output
    assert capture == []


def test_cancel_moves_nothing(
    runner: CliRunner,
    cfg_dir: pathlib.Path,
    vault: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Esc/Ctrl-C → exit 130 → no-op, exit 0."""
    write_cfg(cfg_dir, vault)
    _seed_note(vault, "foo.md", "foo")
    _patch_fzf_present(monkeypatch)
    _patch_fzf(monkeypatch, stdout="", returncode=130)

    result = runner.invoke(cli, ["archive"])
    assert result.exit_code == 0, result.output
    assert (vault / "foo.md").exists()
    assert not (vault / ".treebeard" / "archive").exists()


def test_single_selection_archives(
    runner: CliRunner,
    cfg_dir: pathlib.Path,
    vault: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    write_cfg(cfg_dir, vault)
    target = _seed_note(vault, "foo.md", "foo")
    _patch_fzf_present(monkeypatch)
    _patch_fzf(monkeypatch, stdout=f"foo  just now\t{target}\n")
    _freeze_archive_clock(monkeypatch, datetime(2026, 5, 7, 14, 23, 5, tzinfo=UTC))

    result = runner.invoke(cli, ["archive"])
    assert result.exit_code == 0, result.output

    assert not target.exists()
    archived = vault / ".treebeard" / "archive" / "2026-05-07T14-23-05Z__foo.md"
    assert archived.exists()
    assert "archived foo.md" in result.output


def test_multi_selection_shares_timestamp(
    runner: CliRunner,
    cfg_dir: pathlib.Path,
    vault: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Multi-archive uses one timestamp prefix for the whole batch."""
    write_cfg(cfg_dir, vault)
    a = _seed_note(vault, "a.md", "a")
    b = _seed_note(vault, "b.md", "b")
    _patch_fzf_present(monkeypatch)
    _patch_fzf(monkeypatch, stdout=f"a  just now\t{a}\nb  just now\t{b}\n")
    _freeze_archive_clock(monkeypatch, datetime(2026, 5, 7, 14, 23, 5, tzinfo=UTC))

    result = runner.invoke(cli, ["archive"])
    assert result.exit_code == 0, result.output

    archive_dir = vault / ".treebeard" / "archive"
    assert (archive_dir / "2026-05-07T14-23-05Z__a.md").exists()
    assert (archive_dir / "2026-05-07T14-23-05Z__b.md").exists()
    assert not a.exists()
    assert not b.exists()
    assert "archived 2 notes" in result.output


def test_repeat_archive_of_same_name(
    runner: CliRunner,
    cfg_dir: pathlib.Path,
    vault: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Recreating and re-archiving a same-named note produces a distinct
    file because the timestamp prefix differs."""
    write_cfg(cfg_dir, vault)
    target = _seed_note(vault, "foo.md", "foo")
    _patch_fzf_present(monkeypatch)
    _patch_fzf(monkeypatch, stdout=f"foo  just now\t{target}\n")
    _freeze_archive_clock(monkeypatch, datetime(2026, 5, 7, 14, 23, 5, tzinfo=UTC))

    result = runner.invoke(cli, ["archive"])
    assert result.exit_code == 0, result.output

    # Recreate at the same path and archive again with a later clock.
    target = _seed_note(vault, "foo.md", "foo", body="round two\n")
    _patch_fzf(monkeypatch, stdout=f"foo  just now\t{target}\n")
    _freeze_archive_clock(monkeypatch, datetime(2026, 5, 7, 15, 0, 0, tzinfo=UTC))

    result = runner.invoke(cli, ["archive"])
    assert result.exit_code == 0, result.output

    archive_dir = vault / ".treebeard" / "archive"
    archived = sorted(p.name for p in archive_dir.iterdir())
    assert archived == [
        "2026-05-07T14-23-05Z__foo.md",
        "2026-05-07T15-00-00Z__foo.md",
    ]


def test_archive_dir_created_lazily(
    runner: CliRunner,
    cfg_dir: pathlib.Path,
    vault: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`.treebeard/archive/` only appears once an archive actually happens."""
    write_cfg(cfg_dir, vault)
    target = _seed_note(vault, "foo.md", "foo")
    assert not (vault / ".treebeard" / "archive").exists()

    _patch_fzf_present(monkeypatch)
    _patch_fzf(monkeypatch, stdout=f"foo  just now\t{target}\n")

    result = runner.invoke(cli, ["archive"])
    assert result.exit_code == 0, result.output
    assert (vault / ".treebeard" / "archive").is_dir()


def test_archived_file_disappears_from_listing(
    runner: CliRunner,
    cfg_dir: pathlib.Path,
    vault: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`list_recent_notes` is non-recursive, so archived files vanish
    from `treebeard open` / `treebeard grep` automatically."""
    write_cfg(cfg_dir, vault)
    keep = _seed_note(vault, "keep.md", "keep")
    drop = _seed_note(vault, "drop.md", "drop")
    _patch_fzf_present(monkeypatch)
    _patch_fzf(monkeypatch, stdout=f"drop  just now\t{drop}\n")

    result = runner.invoke(cli, ["archive"])
    assert result.exit_code == 0, result.output

    listed = list_recent_notes(vault, None)
    assert keep in listed
    assert drop not in listed


def test_uses_multi_flag(
    runner: CliRunner,
    cfg_dir: pathlib.Path,
    vault: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """fzf must be invoked with `--multi` so Tab marks rows."""
    write_cfg(cfg_dir, vault)
    _seed_note(vault, "foo.md", "foo")
    _patch_fzf_present(monkeypatch)
    capture: list[dict[str, Any]] = []
    _patch_fzf(monkeypatch, stdout="", returncode=130, capture=capture)

    runner.invoke(cli, ["archive"])
    assert capture, "fzf was not invoked"
    cmd = capture[0]["args"][0]
    assert "--multi" in cmd


def test_auto_commit_records_archive(
    runner: CliRunner,
    cfg_dir: pathlib.Path,
    vault: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The CLI's auto-commit hook commits the rename with `archive:` subject."""
    write_cfg(cfg_dir, vault)
    target = _seed_note(vault, "foo.md", "foo")
    _patch_fzf_present(monkeypatch)
    _patch_fzf(monkeypatch, stdout=f"foo  just now\t{target}\n")

    result = runner.invoke(cli, ["archive"])
    assert result.exit_code == 0, result.output

    subject = _git_log_subject(vault)
    assert re.match(r"^archive: \d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$", subject), subject


def test_preview_uses_configured_previewer(
    runner: CliRunner,
    cfg_dir: pathlib.Path,
    vault: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    write_cfg(cfg_dir, vault, previewer="bat")
    _seed_note(vault, "foo.md", "foo")
    _patch_fzf_present(monkeypatch)
    capture: list[dict[str, Any]] = []
    _patch_fzf(monkeypatch, stdout="", returncode=130, capture=capture)
    runner.invoke(cli, ["archive"])
    assert capture, "fzf was not invoked"
    cmd = capture[0]["args"][0]
    preview_flag = next((a for a in cmd if a.startswith("--preview=")), None)
    assert preview_flag is not None
    assert "bat" in preview_flag


def test_archive_drops_index_below_threshold(
    runner: CliRunner,
    cfg_dir: pathlib.Path,
    vault: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Archiving a note that brings a tag's count below THRESHOLD (3)
    must auto-archive the now-orphaned per-tag index, not leave it at
    root pointing at the archived note."""
    write_cfg(cfg_dir, vault)
    _seed_note(vault, "alpha.md", "Alpha", tags=["foo"])
    _seed_note(vault, "beta.md", "Beta", tags=["foo"])
    target = _seed_note(vault, "gamma.md", "Gamma", tags=["foo"])

    # Materialize the index up front so the close-hook indexer pass
    # finds it stale rather than skipping (count=3, then archive → 2).
    build_indexes(vault, now=datetime(2026, 5, 7, 14, 0, 0, tzinfo=UTC))
    assert (vault / "foo.md").exists()

    _patch_fzf_present(monkeypatch)
    _patch_fzf(monkeypatch, stdout=f"gamma  just now\t{target}\n")
    _freeze_archive_clock(monkeypatch, datetime(2026, 5, 7, 14, 23, 5, tzinfo=UTC))

    result = runner.invoke(cli, ["archive"])
    assert result.exit_code == 0, result.output

    assert not target.exists()
    assert not (vault / "foo.md").exists(), "stale index left at vault root"
    archive_dir = vault / ".treebeard" / "archive"
    assert (archive_dir / "2026-05-07T14-23-05Z__gamma.md").exists()
    # The indexer pass runs inside `_on_close` with a real-clock `now`,
    # so the stale index gets a wallclock stamp distinct from the
    # frozen one used for the user-archived note. Assert by suffix.
    assert any(p.name.endswith("__foo.md") for p in archive_dir.iterdir())
