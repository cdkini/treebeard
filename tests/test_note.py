"""Tests for `om note`."""

from __future__ import annotations

import os
import stat
from datetime import UTC, datetime
from pathlib import Path

import pytest
from click.testing import CliRunner

from om.cli import cli
from om.commands import note as note_cmd
from om.config import Config

FROZEN_NOW = datetime(2026, 5, 7, 14, 23, 5, tzinfo=UTC)
FROZEN_LATER = datetime(2026, 5, 7, 15, 0, 0, tzinfo=UTC)


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    v = tmp_path / "vault"
    (v / ".om").mkdir(parents=True)
    return v


@pytest.fixture
def cfg_dir(tmp_path: Path) -> Path:
    d = tmp_path / "cfg"
    d.mkdir()
    return d


def _write_cfg(cfg_dir: Path, vault: Path, editor: str) -> None:
    """Pre-seed `cfg_dir/config.toml` via the Config dataclass."""
    Config(vault=vault, editor=editor).save(str(cfg_dir))


@pytest.fixture
def freeze_now(monkeypatch: pytest.MonkeyPatch) -> list[datetime]:
    """Hand `_now_utc` a queue of timestamps; pops front per call, repeats last when empty."""
    queue: list[datetime] = [FROZEN_NOW]

    def fake_now() -> datetime:
        return queue.pop(0) if len(queue) > 1 else queue[0]

    monkeypatch.setattr(note_cmd, "_now_utc", fake_now)
    return queue


def _make_script(tmp_path: Path, body: str, name: str = "edit.sh") -> Path:
    """Write an executable shell script that runs `body` with $1 == file path.

    Skips any leading `+...` arg so the script can stand in for vim/nvim,
    which `om` invokes as `vim + <path>` to land the cursor at EOF.
    """
    script = tmp_path / name
    preamble = 'while [ "${1#+}" != "$1" ]; do shift; done\n'
    script.write_text(f"#!/bin/sh\n{preamble}{body}\n", encoding="utf-8")
    script.chmod(script.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return script


def _appender(tmp_path: Path, payload: str = "edited\n", name: str = "appender.sh") -> Path:
    return _make_script(tmp_path, f'printf "%s" "{payload}" >> "$1"', name=name)


def _title_setter(tmp_path: Path, new_title: str, name: str = "set_title.sh") -> Path:
    """Replace `title:` line in the file (assumes a leading frontmatter block)."""
    body = (
        "tmp=$(mktemp)\n"
        f"awk -v t='title: {new_title}' "
        "'NR==FNR{next} /^title:/ && !done {print t; done=1; next} {print}' "
        '"$1" "$1" > "$tmp" && mv "$tmp" "$1"\n'
    )
    return _make_script(tmp_path, body, name=name)


def test_help(runner: CliRunner) -> None:
    result = runner.invoke(cli, ["note", "--help"])
    assert result.exit_code == 0, result.output
    assert "note" in result.output


def test_creates_named_file_with_frontmatter(
    runner: CliRunner,
    cfg_dir: Path,
    vault: Path,
    tmp_path: Path,
    freeze_now: list[datetime],
) -> None:
    del freeze_now
    script = _appender(tmp_path)
    _write_cfg(cfg_dir, vault, str(script))
    result = runner.invoke(cli, ["note", "--config-dir", str(cfg_dir), "hello"])
    assert result.exit_code == 0, result.output
    path = vault / "hello.md"
    assert path.exists()
    text = path.read_text(encoding="utf-8")
    expected_frontmatter = (
        "---\n"
        "title: hello\n"
        "source: user\n"
        "created_at: 2026-05-07T14:23:05Z\n"
        "updated_at: 2026-05-07T14:23:05Z\n"
        "tags: []\n"
        "---\n"
    )
    # The template has two trailing blank lines so the cursor (passed `+`)
    # lands at the second blank line below the frontmatter.
    assert text == expected_frontmatter + "\n\nedited\n"
    assert str(path) in result.output


def test_named_discards_when_unchanged(
    runner: CliRunner, cfg_dir: Path, vault: Path, freeze_now: list[datetime]
) -> None:
    del freeze_now
    _write_cfg(cfg_dir, vault, "true")
    result = runner.invoke(cli, ["note", "--config-dir", str(cfg_dir), "hello"])
    assert result.exit_code == 0, result.output
    assert not (vault / "hello.md").exists()
    assert "discarded empty note" in result.output


def test_named_discards_on_editor_failure(
    runner: CliRunner, cfg_dir: Path, vault: Path, freeze_now: list[datetime]
) -> None:
    del freeze_now
    _write_cfg(cfg_dir, vault, "false")
    result = runner.invoke(cli, ["note", "--config-dir", str(cfg_dir), "hello"])
    assert result.exit_code != 0
    assert not (vault / "hello.md").exists()


def test_slugifies_name(
    runner: CliRunner,
    cfg_dir: Path,
    vault: Path,
    tmp_path: Path,
    freeze_now: list[datetime],
) -> None:
    del freeze_now
    script = _appender(tmp_path)
    _write_cfg(cfg_dir, vault, str(script))
    result = runner.invoke(cli, ["note", "--config-dir", str(cfg_dir), "Sprint Planning!"])
    assert result.exit_code == 0, result.output
    path = vault / "sprint-planning.md"
    assert path.exists()
    assert "title: Sprint Planning!\n" in path.read_text(encoding="utf-8")


def test_strips_md_extension(
    runner: CliRunner,
    cfg_dir: Path,
    vault: Path,
    tmp_path: Path,
    freeze_now: list[datetime],
) -> None:
    del freeze_now
    script = _appender(tmp_path)
    _write_cfg(cfg_dir, vault, str(script))
    r1 = runner.invoke(cli, ["note", "--config-dir", str(cfg_dir), "foo.md"])
    assert r1.exit_code == 0, r1.output
    path = vault / "foo.md"
    assert path.exists()
    assert "title: foo\n" in path.read_text(encoding="utf-8")
    # Re-running with the bare name reopens the same file.
    _write_cfg(cfg_dir, vault, "true")
    r2 = runner.invoke(cli, ["note", "--config-dir", str(cfg_dir), "foo"])
    assert r2.exit_code == 0, r2.output
    assert sorted(p.name for p in vault.glob("*.md")) == ["foo.md"]


def test_unnamed_falls_back_to_timestamp_when_title_left_empty(
    runner: CliRunner,
    cfg_dir: Path,
    vault: Path,
    tmp_path: Path,
    freeze_now: list[datetime],
) -> None:
    del freeze_now
    script = _appender(tmp_path, payload="just a body\n")
    _write_cfg(cfg_dir, vault, str(script))
    result = runner.invoke(cli, ["note", "--config-dir", str(cfg_dir)])
    assert result.exit_code == 0, result.output
    path = vault / "scratch-2026-05-07t14-23-05.md"
    assert path.exists()
    text = path.read_text(encoding="utf-8")
    assert "title: Scratch 2026-05-07T14-23-05\n" in text
    assert text.endswith("just a body\n")
    # Draft tempfile cleaned up.
    drafts = vault / ".om" / "drafts"
    assert list(drafts.iterdir()) == []


def test_unnamed_uses_edited_title_for_filename(
    runner: CliRunner,
    cfg_dir: Path,
    vault: Path,
    tmp_path: Path,
    freeze_now: list[datetime],
) -> None:
    del freeze_now
    script = _title_setter(tmp_path, "My Great Idea")
    _write_cfg(cfg_dir, vault, str(script))
    result = runner.invoke(cli, ["note", "--config-dir", str(cfg_dir)])
    assert result.exit_code == 0, result.output
    path = vault / "my-great-idea.md"
    assert path.exists()
    assert "title: My Great Idea\n" in path.read_text(encoding="utf-8")
    # No timestamp file.
    assert not (vault / "scratch-2026-05-07t14-23-05.md").exists()


def test_unnamed_discards_when_unchanged(
    runner: CliRunner, cfg_dir: Path, vault: Path, freeze_now: list[datetime]
) -> None:
    del freeze_now
    _write_cfg(cfg_dir, vault, "true")
    result = runner.invoke(cli, ["note", "--config-dir", str(cfg_dir)])
    assert result.exit_code == 0, result.output
    assert "discarded empty note" in result.output
    drafts = vault / ".om" / "drafts"
    assert list(drafts.iterdir()) == []
    assert list(vault.glob("*.md")) == []


def test_unnamed_collision_keeps_draft(
    runner: CliRunner,
    cfg_dir: Path,
    vault: Path,
    tmp_path: Path,
    freeze_now: list[datetime],
) -> None:
    del freeze_now
    (vault / "todo.md").write_text("pre-existing\n", encoding="utf-8")
    script = _title_setter(tmp_path, "todo")
    _write_cfg(cfg_dir, vault, str(script))
    result = runner.invoke(cli, ["note", "--config-dir", str(cfg_dir)])
    assert result.exit_code != 0
    assert "already exists" in result.output
    assert "draft kept at" in result.output
    # Existing file untouched.
    assert (vault / "todo.md").read_text(encoding="utf-8") == "pre-existing\n"
    # Draft survives in the drafts dir.
    drafts = list((vault / ".om" / "drafts").iterdir())
    assert len(drafts) == 1


def test_reopen_does_not_clobber_when_unchanged(
    runner: CliRunner, cfg_dir: Path, vault: Path, freeze_now: list[datetime]
) -> None:
    del freeze_now
    path = vault / "todo.md"
    original = (
        "---\n"
        "title: My Todo\n"
        "source: user\n"
        "created_at: 2020-01-01T00:00:00Z\n"
        "updated_at: 2020-01-01T00:00:00Z\n"
        "tags: [a, b]\n"
        "extra: keep me\n"
        "---\n"
        "body content\n"
    )
    path.write_text(original, encoding="utf-8")

    _write_cfg(cfg_dir, vault, "true")
    result = runner.invoke(cli, ["note", "--config-dir", str(cfg_dir), "todo"])
    assert result.exit_code == 0, result.output
    assert path.read_text(encoding="utf-8") == original


def test_reopen_bumps_updated_at_when_mtime_moves(
    runner: CliRunner,
    cfg_dir: Path,
    vault: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    queue: list[datetime] = [FROZEN_NOW, FROZEN_LATER]
    monkeypatch.setattr(note_cmd, "_now_utc", lambda: queue.pop(0) if len(queue) > 1 else queue[0])

    path = vault / "todo.md"
    path.write_text(
        "---\n"
        "title: My Todo\n"
        "source: user\n"
        "created_at: 2020-01-01T00:00:00Z\n"
        "updated_at: 2020-01-01T00:00:00Z\n"
        "tags: [a]\n"
        "---\n"
        "body\n",
        encoding="utf-8",
    )
    # Force older mtime so any append moves it.
    old = path.stat().st_mtime - 60
    os.utime(path, (old, old))

    script = _appender(tmp_path)
    _write_cfg(cfg_dir, vault, str(script))
    result = runner.invoke(cli, ["note", "--config-dir", str(cfg_dir), "todo"])
    assert result.exit_code == 0, result.output
    text = path.read_text(encoding="utf-8")
    assert "created_at: 2020-01-01T00:00:00Z\n" in text
    assert "updated_at: 2026-05-07T15:00:00Z\n" in text
    assert "updated_at: 2020-01-01T00:00:00Z\n" not in text
    assert text.endswith("body\nedited\n")


def test_reopen_with_malformed_frontmatter_is_noop(
    runner: CliRunner,
    cfg_dir: Path,
    vault: Path,
    tmp_path: Path,
    freeze_now: list[datetime],
) -> None:
    del freeze_now
    path = vault / "raw.md"
    path.write_text("just a body, no frontmatter\n", encoding="utf-8")
    old = path.stat().st_mtime - 60
    os.utime(path, (old, old))

    script = _appender(tmp_path, payload="more\n")
    _write_cfg(cfg_dir, vault, str(script))
    result = runner.invoke(cli, ["note", "--config-dir", str(cfg_dir), "raw"])
    assert result.exit_code == 0, result.output
    assert path.read_text(encoding="utf-8") == "just a body, no frontmatter\nmore\n"


def test_errors_when_no_vault_configured(runner: CliRunner, tmp_path: Path) -> None:
    empty_cfg = tmp_path / "empty"
    empty_cfg.mkdir()
    result = runner.invoke(cli, ["note", "--config-dir", str(empty_cfg), "hi"])
    assert result.exit_code != 0
    assert "no vault configured" in result.output


def test_errors_on_empty_slug(
    runner: CliRunner, cfg_dir: Path, vault: Path, freeze_now: list[datetime]
) -> None:
    del freeze_now
    _write_cfg(cfg_dir, vault, "true")
    result = runner.invoke(cli, ["note", "--config-dir", str(cfg_dir), "!!!"])
    assert result.exit_code != 0
    assert "empty slug" in result.output


def test_named_keeps_tags_when_user_adds_them(
    runner: CliRunner,
    cfg_dir: Path,
    vault: Path,
    tmp_path: Path,
    freeze_now: list[datetime],
) -> None:
    del freeze_now
    body = (
        "tmp=$(mktemp)\n"
        "awk '/^tags:/ {print \"tags: [foo, bar]\"; next} {print}' "
        '"$1" > "$tmp" && mv "$tmp" "$1"\n'
    )
    script = _make_script(tmp_path, body, name="add_tags.sh")
    _write_cfg(cfg_dir, vault, str(script))
    result = runner.invoke(cli, ["note", "--config-dir", str(cfg_dir), "hello"])
    assert result.exit_code == 0, result.output
    text = (vault / "hello.md").read_text(encoding="utf-8")
    assert "tags: [foo, bar]\n" in text
