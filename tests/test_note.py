"""Tests for `om note` (CLI integration — pure logic lives in test_todos / test_frontmatter)."""

from __future__ import annotations

import os
import pathlib

from click.testing import CliRunner

from om.cli import cli
from tests.conftest import FROZEN_LATER, FROZEN_NOW, EditorFake, write_cfg


def append(payload: str) -> EditorFake:
    def _do(_ed: str, p: pathlib.Path) -> None:
        p.write_text(p.read_text(encoding="utf-8") + payload, encoding="utf-8")

    return _do


def set_title(new_title: str) -> EditorFake:
    def _do(_ed: str, path: pathlib.Path) -> None:
        text = path.read_text(encoding="utf-8")
        path.write_text(
            text.replace("title: \n", f"title: {new_title}\n", 1)
            if "title: \n" in text
            else text.replace("title:", f"title: {new_title}", 1),
            encoding="utf-8",
        )

    return _do


def test_help(runner: CliRunner) -> None:
    result = runner.invoke(cli, ["note", "--help"])
    assert result.exit_code == 0, result.output
    assert "note" in result.output


def test_creates_named_file_with_frontmatter(
    runner: CliRunner,
    cfg_dir: pathlib.Path,
    vault: pathlib.Path,
    fake_editor: list[EditorFake],
    freeze_now: list,
) -> None:
    del freeze_now
    fake_editor.append(append("edited\n"))
    write_cfg(cfg_dir, vault)
    result = runner.invoke(cli, ["note", "--config-dir", str(cfg_dir), "hello"])
    assert result.exit_code == 0, result.output
    path = vault / "hello.md"
    expected_frontmatter = (
        "---\n"
        "title: hello\n"
        "source: user\n"
        "created_at: 2026-05-07T14:23:05Z\n"
        "updated_at: 2026-05-07T14:23:05Z\n"
        "tags: []\n"
        "---\n"
    )
    assert path.read_text(encoding="utf-8") == expected_frontmatter + "\n\nedited\n"
    assert str(path) in result.output


def test_named_discards_when_editor_makes_no_change(
    runner: CliRunner,
    cfg_dir: pathlib.Path,
    vault: pathlib.Path,
    fake_editor: list[EditorFake],
    freeze_now: list,
) -> None:
    del freeze_now, fake_editor  # empty queue → editor is a no-op
    write_cfg(cfg_dir, vault)
    result = runner.invoke(cli, ["note", "--config-dir", str(cfg_dir), "hello"])
    assert result.exit_code == 0, result.output
    assert not (vault / "hello.md").exists()
    assert "discarded empty note" in result.output


def test_named_discards_on_editor_failure_via_real_subprocess(
    runner: CliRunner, cfg_dir: pathlib.Path, vault: pathlib.Path, freeze_now: list
) -> None:
    """Exercises the real subprocess.run path (no fake_editor patch)."""
    del freeze_now
    write_cfg(cfg_dir, vault, editor="false")
    result = runner.invoke(cli, ["note", "--config-dir", str(cfg_dir), "hello"])
    assert result.exit_code != 0
    assert not (vault / "hello.md").exists()


def test_unchanged_via_real_subprocess(
    runner: CliRunner, cfg_dir: pathlib.Path, vault: pathlib.Path, freeze_now: list
) -> None:
    """Exercises the real subprocess.run path with a successful no-op editor."""
    del freeze_now
    write_cfg(cfg_dir, vault, editor="true")
    result = runner.invoke(cli, ["note", "--config-dir", str(cfg_dir), "hello"])
    assert result.exit_code == 0, result.output
    assert not (vault / "hello.md").exists()
    assert "discarded empty note" in result.output


def test_slugifies_name(
    runner: CliRunner,
    cfg_dir: pathlib.Path,
    vault: pathlib.Path,
    fake_editor: list[EditorFake],
    freeze_now: list,
) -> None:
    del freeze_now
    fake_editor.append(append("body\n"))
    write_cfg(cfg_dir, vault)
    result = runner.invoke(cli, ["note", "--config-dir", str(cfg_dir), "Sprint Planning!"])
    assert result.exit_code == 0, result.output
    path = vault / "sprint-planning.md"
    assert "title: Sprint Planning!\n" in path.read_text(encoding="utf-8")


def test_strips_md_extension_and_reopens(
    runner: CliRunner,
    cfg_dir: pathlib.Path,
    vault: pathlib.Path,
    fake_editor: list[EditorFake],
    freeze_now: list,
) -> None:
    del freeze_now
    fake_editor.append(append("body\n"))
    write_cfg(cfg_dir, vault)
    r1 = runner.invoke(cli, ["note", "--config-dir", str(cfg_dir), "foo.md"])
    assert r1.exit_code == 0, r1.output
    assert (vault / "foo.md").exists()
    # Reopen with bare name (no editor edit) — should not create a duplicate.
    r2 = runner.invoke(cli, ["note", "--config-dir", str(cfg_dir), "foo"])
    assert r2.exit_code == 0, r2.output
    assert sorted(p.name for p in vault.glob("*.md")) == ["foo.md"]


def test_unnamed_falls_back_to_timestamp(
    runner: CliRunner,
    cfg_dir: pathlib.Path,
    vault: pathlib.Path,
    fake_editor: list[EditorFake],
    freeze_now: list,
) -> None:
    del freeze_now
    fake_editor.append(append("just a body\n"))
    write_cfg(cfg_dir, vault)
    result = runner.invoke(cli, ["note", "--config-dir", str(cfg_dir)])
    assert result.exit_code == 0, result.output
    path = vault / "scratch-2026-05-07t14-23-05.md"
    text = path.read_text(encoding="utf-8")
    assert "title: Scratch 2026-05-07T14-23-05\n" in text
    assert text.endswith("just a body\n")
    assert list((vault / ".om" / "drafts").iterdir()) == []


def test_unnamed_uses_edited_title_for_filename(
    runner: CliRunner,
    cfg_dir: pathlib.Path,
    vault: pathlib.Path,
    fake_editor: list[EditorFake],
    freeze_now: list,
) -> None:
    del freeze_now
    fake_editor.append(set_title("My Great Idea"))
    write_cfg(cfg_dir, vault)
    result = runner.invoke(cli, ["note", "--config-dir", str(cfg_dir)])
    assert result.exit_code == 0, result.output
    assert (vault / "my-great-idea.md").exists()
    assert not (vault / "scratch-2026-05-07t14-23-05.md").exists()


def test_unnamed_collision_keeps_draft(
    runner: CliRunner,
    cfg_dir: pathlib.Path,
    vault: pathlib.Path,
    fake_editor: list[EditorFake],
    freeze_now: list,
) -> None:
    del freeze_now
    (vault / "todo.md").write_text("pre-existing\n", encoding="utf-8")
    fake_editor.append(set_title("todo"))
    write_cfg(cfg_dir, vault)
    result = runner.invoke(cli, ["note", "--config-dir", str(cfg_dir)])
    assert result.exit_code != 0
    assert "already exists" in result.output
    assert "draft kept at" in result.output
    assert (vault / "todo.md").read_text(encoding="utf-8") == "pre-existing\n"
    assert len(list((vault / ".om" / "drafts").iterdir())) == 1


def test_reopen_does_not_clobber_when_unchanged(
    runner: CliRunner,
    cfg_dir: pathlib.Path,
    vault: pathlib.Path,
    fake_editor: list[EditorFake],
    freeze_now: list,
) -> None:
    del freeze_now, fake_editor  # no edit
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
    write_cfg(cfg_dir, vault)
    result = runner.invoke(cli, ["note", "--config-dir", str(cfg_dir), "todo"])
    assert result.exit_code == 0, result.output
    assert path.read_text(encoding="utf-8") == original


def test_reopen_bumps_updated_at(
    runner: CliRunner,
    cfg_dir: pathlib.Path,
    vault: pathlib.Path,
    fake_editor: list[EditorFake],
    freeze_now: list,
) -> None:
    freeze_now[:] = [FROZEN_NOW, FROZEN_LATER]
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
    old = path.stat().st_mtime - 60
    os.utime(path, (old, old))

    fake_editor.append(append("edited\n"))
    write_cfg(cfg_dir, vault)
    result = runner.invoke(cli, ["note", "--config-dir", str(cfg_dir), "todo"])
    assert result.exit_code == 0, result.output
    text = path.read_text(encoding="utf-8")
    assert "created_at: 2020-01-01T00:00:00Z\n" in text
    assert "updated_at: 2026-05-07T15:00:00Z\n" in text
    assert text.endswith("body\nedited\n")


def test_reopen_with_malformed_frontmatter_is_noop(
    runner: CliRunner,
    cfg_dir: pathlib.Path,
    vault: pathlib.Path,
    fake_editor: list[EditorFake],
    freeze_now: list,
) -> None:
    del freeze_now
    path = vault / "raw.md"
    path.write_text("just a body, no frontmatter\n", encoding="utf-8")
    old = path.stat().st_mtime - 60
    os.utime(path, (old, old))

    fake_editor.append(append("more\n"))
    write_cfg(cfg_dir, vault)
    result = runner.invoke(cli, ["note", "--config-dir", str(cfg_dir), "raw"])
    assert result.exit_code == 0, result.output
    assert path.read_text(encoding="utf-8") == "just a body, no frontmatter\nmore\n"


def test_errors_when_no_vault_configured(runner: CliRunner, tmp_path: pathlib.Path) -> None:
    empty_cfg = tmp_path / "empty"
    empty_cfg.mkdir()
    result = runner.invoke(cli, ["note", "--config-dir", str(empty_cfg), "hi"])
    assert result.exit_code != 0
    assert "no vault configured" in result.output


def test_errors_on_empty_slug(
    runner: CliRunner, cfg_dir: pathlib.Path, vault: pathlib.Path, freeze_now: list
) -> None:
    del freeze_now
    write_cfg(cfg_dir, vault)
    result = runner.invoke(cli, ["note", "--config-dir", str(cfg_dir), "!!!"])
    assert result.exit_code != 0
    assert "empty slug" in result.output


def test_named_keeps_user_added_tags(
    runner: CliRunner,
    cfg_dir: pathlib.Path,
    vault: pathlib.Path,
    fake_editor: list[EditorFake],
    freeze_now: list,
) -> None:
    del freeze_now

    def add_tags(_ed: str, p: pathlib.Path) -> None:
        text = p.read_text(encoding="utf-8")
        p.write_text(text.replace("tags: []\n", "tags: [foo, bar]\n", 1), encoding="utf-8")

    fake_editor.append(add_tags)
    write_cfg(cfg_dir, vault)
    result = runner.invoke(cli, ["note", "--config-dir", str(cfg_dir), "hello"])
    assert result.exit_code == 0, result.output
    assert "tags: [foo, bar]\n" in (vault / "hello.md").read_text(encoding="utf-8")
