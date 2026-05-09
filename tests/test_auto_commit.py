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


def _setup_upstream(vault: pathlib.Path, tmp_path: pathlib.Path) -> pathlib.Path:
    """Make `vault` track a fresh bare repo as `origin/main`, with one
    baseline commit pushed. Returns the bare-repo path."""
    bare = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", "--quiet", str(bare)], check=True)
    (vault / "README.md").write_text("seed\n", encoding="utf-8")
    _git(vault, "add", "-A")
    _git(vault, "commit", "--quiet", "-m", "seed")
    _git(vault, "remote", "add", "origin", str(bare))
    _git(vault, "push", "--quiet", "-u", "origin", "main")
    return bare


def test_warns_at_default_threshold(
    runner: CliRunner,
    cfg_dir: pathlib.Path,
    vault: pathlib.Path,
    tmp_path: pathlib.Path,
    fake_editor: list[EditorFake],
    freeze_now: list,
) -> None:
    del freeze_now
    _setup_upstream(vault, tmp_path)
    write_cfg(cfg_dir, vault)

    # Each `om note` produces exactly one auto-commit; ten of them
    # puts us at the default threshold. The tenth invocation should
    # print the warning.
    outputs: list[str] = []
    for i in range(10):
        fake_editor.append(_append(f"body-{i}\n"))
        result = runner.invoke(cli, ["note", "--config-dir", str(cfg_dir), f"n{i}"])
        assert result.exit_code == 0, result.output
        outputs.append(result.output)

    assert "10 unsynced commits" in outputs[-1]
    assert "om sync" in outputs[-1]


def test_no_warning_below_threshold(
    runner: CliRunner,
    cfg_dir: pathlib.Path,
    vault: pathlib.Path,
    tmp_path: pathlib.Path,
    fake_editor: list[EditorFake],
    freeze_now: list,
) -> None:
    del freeze_now
    _setup_upstream(vault, tmp_path)
    write_cfg(cfg_dir, vault)

    outputs: list[str] = []
    for i in range(9):
        fake_editor.append(_append(f"body-{i}\n"))
        result = runner.invoke(cli, ["note", "--config-dir", str(cfg_dir), f"n{i}"])
        assert result.exit_code == 0, result.output
        outputs.append(result.output)

    assert "unsynced commits" not in outputs[-1]


def test_threshold_is_configurable(
    runner: CliRunner,
    cfg_dir: pathlib.Path,
    vault: pathlib.Path,
    tmp_path: pathlib.Path,
    fake_editor: list[EditorFake],
    freeze_now: list,
) -> None:
    """A user-set `sync_warn_threshold` overrides the default of 10."""
    del freeze_now
    _setup_upstream(vault, tmp_path)
    write_cfg(cfg_dir, vault, sync_warn_threshold=3)

    outputs: list[str] = []
    for i in range(3):
        fake_editor.append(_append(f"body-{i}\n"))
        result = runner.invoke(cli, ["note", "--config-dir", str(cfg_dir), f"n{i}"])
        assert result.exit_code == 0, result.output
        outputs.append(result.output)

    assert "unsynced commits" not in outputs[-2]
    assert "3 unsynced commits" in outputs[-1]


def test_silent_without_upstream(
    runner: CliRunner,
    cfg_dir: pathlib.Path,
    vault: pathlib.Path,
    fake_editor: list[EditorFake],
    freeze_now: list,
) -> None:
    """Vault has no remote configured — nothing to compare against, so
    even a long stack of local commits must not trigger the warning."""
    del freeze_now
    write_cfg(cfg_dir, vault)

    outputs: list[str] = []
    for i in range(11):
        fake_editor.append(_append(f"body-{i}\n"))
        result = runner.invoke(cli, ["note", "--config-dir", str(cfg_dir), f"n{i}"])
        assert result.exit_code == 0, result.output
        outputs.append(result.output)

    assert "unsynced commits" not in outputs[-1]


def test_silent_when_upstream_ref_missing(
    runner: CliRunner,
    cfg_dir: pathlib.Path,
    vault: pathlib.Path,
    tmp_path: pathlib.Path,
    fake_editor: list[EditorFake],
    freeze_now: list,
) -> None:
    """A remote URL is configured but we've never fetched, so `@{u}` is
    unresolvable. The hook must stay silent rather than crash."""
    del freeze_now
    # Seed one commit so HEAD exists, then point at a remote we never push to.
    (vault / "README.md").write_text("seed\n", encoding="utf-8")
    _git(vault, "add", "-A")
    _git(vault, "commit", "--quiet", "-m", "seed")
    _git(vault, "remote", "add", "origin", str(tmp_path / "nope.git"))
    write_cfg(cfg_dir, vault)

    outputs: list[str] = []
    for i in range(11):
        fake_editor.append(_append(f"body-{i}\n"))
        result = runner.invoke(cli, ["note", "--config-dir", str(cfg_dir), f"n{i}"])
        assert result.exit_code == 0, result.output
        outputs.append(result.output)

    assert "unsynced commits" not in outputs[-1]


# ---------- Post-edit close-hook tests --------------------------------------
#
# Every editor-running command has the post-edit lifecycle (bump
# `updated_at`, reconcile filename) applied to *every* dirty `.md` at the
# vault root, not just the explicit target. The cases below exercise the
# behaviors the porcelain sweep enables.


def _seed_committed(
    vault: pathlib.Path,
    name: str,
    title: str,
    *,
    tags: list[str] | None = None,
    body: str = "body\n",
) -> pathlib.Path:
    """Write a note and commit it so it's part of HEAD."""
    path = vault / name
    tag_line = f"[{', '.join(tags)}]" if tags else "[]"
    path.write_text(
        "---\n"
        f"title: {title}\n"
        "source: user\n"
        "created_at: 2020-01-01T00:00:00Z\n"
        "updated_at: 2020-01-01T00:00:00Z\n"
        f"tags: {tag_line}\n"
        "---\n" + body,
        encoding="utf-8",
    )
    _git(vault, "add", "-A")
    _git(vault, "commit", "--quiet", "-m", f"seed {name}")
    return path


def test_side_jump_target_gets_post_processed(
    runner: CliRunner,
    cfg_dir: pathlib.Path,
    vault: pathlib.Path,
    fake_editor: list[EditorFake],
    freeze_now: list,
) -> None:
    """User opens `primary.md` via `om note`, side-jumps in vim to
    `other.md`, edits its title. The close hook reconciles `other.md`'s
    filename even though `om note` never opened it."""
    del freeze_now
    other = _seed_committed(vault, "other.md", "other")
    primary = _seed_committed(vault, "primary.md", "primary")

    def jump_edit(_ed: str, _p: pathlib.Path) -> None:
        text = other.read_text(encoding="utf-8")
        other.write_text(text.replace("title: other", "title: Renamed"), encoding="utf-8")

    fake_editor.append(jump_edit)
    write_cfg(cfg_dir, vault)
    result = runner.invoke(cli, ["note", "--config-dir", str(cfg_dir), "primary"])
    assert result.exit_code == 0, result.output

    # Other was renamed by reconcile_filename based on its new title.
    assert (vault / "renamed.md").exists()
    assert not other.exists()
    # Primary was untouched (no edit).
    assert primary.exists()


def test_untracked_create_gets_post_processed(
    runner: CliRunner,
    cfg_dir: pathlib.Path,
    vault: pathlib.Path,
    fake_editor: list[EditorFake],
    freeze_now: list,
) -> None:
    """`:w newnote.md` inside vim creates an untracked file; close hook
    must catch it via `--untracked-files=all` and bump its updated_at."""
    del freeze_now
    primary = _seed_committed(vault, "primary.md", "primary")
    new_path = vault / "newly-created.md"

    def create_new(_ed: str, _p: pathlib.Path) -> None:
        new_path.write_text(
            "---\n"
            "title: newly created\n"
            "source: user\n"
            "created_at: 2020-01-01T00:00:00Z\n"
            "updated_at: 2020-01-01T00:00:00Z\n"
            "tags: []\n"
            "---\nbody\n",
            encoding="utf-8",
        )

    fake_editor.append(create_new)
    write_cfg(cfg_dir, vault)
    result = runner.invoke(cli, ["note", "--config-dir", str(cfg_dir), "primary"])
    assert result.exit_code == 0, result.output

    # New file was post-processed: updated_at was bumped.
    assert new_path.exists()
    text = new_path.read_text(encoding="utf-8")
    assert "updated_at: 2020-01-01T00:00:00Z\n" not in text
    assert "updated_at: 2026-05-07T14:23:05Z\n" in text
    # Primary was untouched.
    assert primary.exists()


def test_deleted_file_not_post_processed(
    runner: CliRunner,
    cfg_dir: pathlib.Path,
    vault: pathlib.Path,
    fake_editor: list[EditorFake],
    freeze_now: list,
) -> None:
    """A file deleted during the editor session has nothing to reconcile.
    The hook must skip it without raising."""
    del freeze_now
    target = _seed_committed(vault, "primary.md", "primary")
    other = _seed_committed(vault, "other.md", "other")

    def delete_other(_ed: str, _p: pathlib.Path) -> None:
        other.unlink()

    fake_editor.append(delete_other)
    write_cfg(cfg_dir, vault)
    result = runner.invoke(cli, ["note", "--config-dir", str(cfg_dir), "primary"])
    assert result.exit_code == 0, result.output

    assert not other.exists()
    assert target.exists()


def test_archive_subdir_excluded(
    runner: CliRunner,
    cfg_dir: pathlib.Path,
    vault: pathlib.Path,
    fake_editor: list[EditorFake],
    freeze_now: list,
) -> None:
    """A side-jump into `.om/archive/old.md` must NOT be post-processed —
    archived notes are intentionally frozen."""
    del freeze_now
    primary = _seed_committed(vault, "primary.md", "primary")

    archive_dir = vault / ".om" / "archive"
    archive_dir.mkdir(parents=True, exist_ok=True)
    archived = archive_dir / "old.md"
    archived.write_text(
        "---\n"
        "title: old\n"
        "source: user\n"
        "created_at: 2020-01-01T00:00:00Z\n"
        "updated_at: 2020-01-01T00:00:00Z\n"
        "tags: []\n"
        "---\nbody\n",
        encoding="utf-8",
    )
    _git(vault, "add", "-A")
    _git(vault, "commit", "--quiet", "-m", "seed archive")

    def edit_archived(_ed: str, _p: pathlib.Path) -> None:
        archived.write_text(
            archived.read_text(encoding="utf-8").replace("title: old", "title: Renamed"),
            encoding="utf-8",
        )

    fake_editor.append(edit_archived)
    write_cfg(cfg_dir, vault)
    result = runner.invoke(cli, ["note", "--config-dir", str(cfg_dir), "primary"])
    assert result.exit_code == 0, result.output

    # Archive untouched by reconcile (no rename).
    assert archived.exists()
    assert "title: Renamed" in archived.read_text(encoding="utf-8")
    # Primary was untouched.
    assert primary.exists()


def test_daily_rename_warns_and_preserves_edits(
    runner: CliRunner,
    cfg_dir: pathlib.Path,
    vault: pathlib.Path,
    fake_editor: list[EditorFake],
    freeze_now: list,
    freeze_today: None,
) -> None:
    """User retitles a daily note. The reconcile guard raises
    PostEditAbort; the close hook warns and the user's content edits
    survive (improvement over the old revert behavior, which destroyed
    the user's edits)."""
    del freeze_now, freeze_today
    today_path = _seed_committed(
        vault,
        "2026-05-07.md",
        "2026-05-07",
        tags=["daily"],
        body="\n### TODOs\n\n- [ ] task\n\n### Notes\n\n",
    )

    def retitle_and_edit(_ed: str, _p: pathlib.Path) -> None:
        text = today_path.read_text(encoding="utf-8")
        text = text.replace("title: 2026-05-07", "title: Sprint Retro")
        text += "extra notes\n"
        today_path.write_text(text, encoding="utf-8")

    fake_editor.append(retitle_and_edit)
    write_cfg(cfg_dir, vault)
    result = runner.invoke(cli, ["daily", "--config-dir", str(cfg_dir)])
    assert result.exit_code == 0, result.output

    assert "could not reconcile" in result.output
    assert "daily note filename is protected" in result.output
    # Filename preserved.
    assert today_path.exists()
    text = today_path.read_text(encoding="utf-8")
    # User's edits are still on disk (the new title and the extra notes).
    assert "title: Sprint Retro" in text
    assert text.endswith("extra notes\n")


def test_post_edit_lands_in_same_commit_as_edit(
    runner: CliRunner,
    cfg_dir: pathlib.Path,
    vault: pathlib.Path,
    fake_editor: list[EditorFake],
    freeze_now: list,
) -> None:
    """The post-edit hook must run *before* `commit_all`, so the rename
    and `updated_at` bump end up in the same commit as the user's
    content edits — not split across two."""
    del freeze_now
    seeded = _seed_committed(vault, "old-name.md", "old name")

    def retitle(_ed: str, p: pathlib.Path) -> None:
        del p  # the editor 'opens' a different file, but we mutate via path
        text = seeded.read_text(encoding="utf-8")
        seeded.write_text(text.replace("title: old name", "title: New Name"), encoding="utf-8")

    fake_editor.append(retitle)
    write_cfg(cfg_dir, vault)

    before = _commit_count(vault)
    result = runner.invoke(cli, ["note", "--config-dir", str(cfg_dir), "old-name"])
    assert result.exit_code == 0, result.output

    after = _commit_count(vault)
    assert after == before + 1, "exactly one new commit (no split rename/edit pair)"

    # The single commit shows the rename + content change as a unit.
    show = _git(vault, "show", "--stat", "HEAD")
    assert "new-name.md" in show
    assert "old-name.md" in show

    # Post-conditions on disk.
    assert (vault / "new-name.md").exists()
    assert not seeded.exists()


# ---------- Auto-index close-hook tests -------------------------------------
#
# `_on_close` runs `build_indexes` between the post-edit sweep and the
# auto-commit, so per-tag index notes stay in sync without a separate
# `om index` invocation.


def test_auto_index_runs_on_subcommand(
    runner: CliRunner,
    cfg_dir: pathlib.Path,
    vault: pathlib.Path,
    fake_editor: list[EditorFake],
    freeze_now: list,
) -> None:
    """A subcommand that touches a note in a vault with an eligible tag
    triggers the auto-index pass; the new index file lands in the same
    auto-commit as the user's edit."""
    del freeze_now
    _seed_committed(vault, "alpha.md", "alpha", tags=["foo"])
    _seed_committed(vault, "beta.md", "beta", tags=["foo"])
    _seed_committed(vault, "gamma.md", "gamma", tags=["foo"])
    write_cfg(cfg_dir, vault)

    fake_editor.append(_append("more\n"))
    before = _commit_count(vault)
    result = runner.invoke(cli, ["note", "--config-dir", str(cfg_dir), "alpha"])
    assert result.exit_code == 0, result.output

    # Single new commit covers both the user's edit and the new index file.
    assert _commit_count(vault) == before + 1
    assert (vault / "foo.md").exists()
    show = _git(vault, "show", "--stat", "HEAD")
    assert "foo.md" in show
    assert "alpha.md" in show


def test_auto_index_idempotent_no_extra_commit(
    runner: CliRunner,
    cfg_dir: pathlib.Path,
    vault: pathlib.Path,
    fake_editor: list[EditorFake],
    freeze_now: list,
) -> None:
    """When the index is already up to date and the user's edit is a
    no-op, the hook must not produce a spurious commit."""
    del freeze_now
    _seed_committed(vault, "alpha.md", "alpha", tags=["foo"])
    _seed_committed(vault, "beta.md", "beta", tags=["foo"])
    _seed_committed(vault, "gamma.md", "gamma", tags=["foo"])
    write_cfg(cfg_dir, vault)

    # First invocation: writes the index in the same commit as the edit.
    fake_editor.append(_append("more\n"))
    runner.invoke(cli, ["note", "--config-dir", str(cfg_dir), "alpha"])
    after_first = _commit_count(vault)

    # Second invocation: editor saves without changes; index is already
    # current. Working tree must stay clean and HEAD must not advance.
    fake_editor.append(lambda _ed, _p: None)
    result = runner.invoke(cli, ["note", "--config-dir", str(cfg_dir), "alpha"])
    assert result.exit_code == 0, result.output
    assert _commit_count(vault) == after_first


def test_auto_index_failure_does_not_block_edit(
    runner: CliRunner,
    cfg_dir: pathlib.Path,
    vault: pathlib.Path,
    fake_editor: list[EditorFake],
    freeze_now: list,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A broken `build_indexes` call must not prevent the user's edit
    from being committed. The auto-index is wrapped in its own try so
    its failure is isolated from the auto-commit step."""
    del freeze_now

    def boom(*_a: object, **_kw: object) -> None:
        raise RuntimeError("indexer exploded")

    monkeypatch.setattr(cli_mod, "build_indexes", boom)

    fake_editor.append(_append("body\n"))
    write_cfg(cfg_dir, vault)

    before = _commit_count(vault)
    result = runner.invoke(cli, ["note", "--config-dir", str(cfg_dir), "hello"])
    assert result.exit_code == 0, result.output

    # The user's edit lands in a commit despite the indexer failure.
    assert _commit_count(vault) == before + 1
    assert (vault / "hello.md").exists()
