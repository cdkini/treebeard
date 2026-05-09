"""Tests for `om.git` — focused on `changed_root_md_paths`, the
porcelain-based diff used by the CLI close hook.

The other helpers in `om.git` (commit, sync, etc.) are exercised via
`test_auto_commit.py` and `test_sync.py`, which drive them end-to-end
through the CLI."""

from __future__ import annotations

import pathlib
import subprocess

from om import git


def _git(vault: pathlib.Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=vault,
        check=True,
        capture_output=True,
        text=True,
    ).stdout


def _commit(vault: pathlib.Path, message: str = "snapshot") -> None:
    _git(vault, "add", "-A")
    _git(vault, "commit", "--quiet", "-m", message)


def test_clean_tree_returns_empty(vault: pathlib.Path) -> None:
    (vault / "foo.md").write_text("hi\n", encoding="utf-8")
    _commit(vault)
    assert git.changed_root_md_paths(vault) == []


def test_returns_modified_md(vault: pathlib.Path) -> None:
    path = vault / "foo.md"
    path.write_text("hi\n", encoding="utf-8")
    _commit(vault)
    path.write_text("hi\nmore\n", encoding="utf-8")
    assert git.changed_root_md_paths(vault) == [path]


def test_returns_untracked_md(vault: pathlib.Path) -> None:
    """Newly-created notes (`:w newfile.md` inside vim) must be caught
    even though they aren't yet in the index."""
    path = vault / "fresh.md"
    path.write_text("hi\n", encoding="utf-8")
    assert git.changed_root_md_paths(vault) == [path]


def test_excludes_deleted(vault: pathlib.Path) -> None:
    """A file that no longer exists has nothing to reconcile."""
    path = vault / "gone.md"
    path.write_text("hi\n", encoding="utf-8")
    _commit(vault)
    path.unlink()
    assert git.changed_root_md_paths(vault) == []


def test_uses_new_path_for_renames(vault: pathlib.Path) -> None:
    """`git mv old.md new.md` produces an `R` entry; we want the new path."""
    old = vault / "old.md"
    old.write_text("hi\n", encoding="utf-8")
    _commit(vault)
    _git(vault, "mv", "old.md", "new.md")
    paths = git.changed_root_md_paths(vault)
    assert paths == [vault / "new.md"]


def test_excludes_subdir_md(vault: pathlib.Path) -> None:
    """Vault is flat — anything under `.om/`, `.git/`, or any subdir is
    out of scope. Archive lives under `.om/archive/`."""
    archive_dir = vault / ".om" / "archive"
    archive_dir.mkdir(parents=True, exist_ok=True)
    (archive_dir / "old.md").write_text("hi\n", encoding="utf-8")
    (vault / "root.md").write_text("hi\n", encoding="utf-8")
    paths = git.changed_root_md_paths(vault)
    assert paths == [vault / "root.md"]


def test_excludes_non_md(vault: pathlib.Path) -> None:
    (vault / "foo.txt").write_text("hi\n", encoding="utf-8")
    (vault / "bar.md").write_text("hi\n", encoding="utf-8")
    paths = git.changed_root_md_paths(vault)
    assert paths == [vault / "bar.md"]


def test_handles_filenames_with_spaces(vault: pathlib.Path) -> None:
    """`-z` keeps NUL-delimited records, no quoting — the parser must
    accept paths with spaces verbatim."""
    path = vault / "two words.md"
    path.write_text("hi\n", encoding="utf-8")
    paths = git.changed_root_md_paths(vault)
    assert paths == [path]


def test_returns_multiple_in_one_call(vault: pathlib.Path) -> None:
    a = vault / "a.md"
    b = vault / "b.md"
    a.write_text("a\n", encoding="utf-8")
    b.write_text("b\n", encoding="utf-8")
    paths = git.changed_root_md_paths(vault)
    assert sorted(paths) == sorted([a, b])


def test_ensure_initialized_is_idempotent(vault: pathlib.Path) -> None:
    """Vault fixture already ran `git init`; calling again must short-circuit."""
    head_before = (vault / ".git" / "HEAD").read_text(encoding="utf-8")
    git.ensure_initialized(vault)
    head_after = (vault / ".git" / "HEAD").read_text(encoding="utf-8")
    assert head_before == head_after


def test_ensure_initialized_creates_repo(tmp_path: pathlib.Path) -> None:
    fresh = tmp_path / "fresh"
    fresh.mkdir()
    assert not (fresh / ".git").exists()
    git.ensure_initialized(fresh)
    assert (fresh / ".git").is_dir()


def test_get_config_missing_returns_none(vault: pathlib.Path) -> None:
    """Unset key → None, not empty string or raise."""
    assert git.get_config(vault, "om.nonexistent.key") is None


def test_set_and_get_config_roundtrip(vault: pathlib.Path) -> None:
    git.set_config(vault, "om.test.value", "hello")
    assert git.get_config(vault, "om.test.value") == "hello"


def test_has_changes_clean_then_dirty(vault: pathlib.Path) -> None:
    assert not git.has_changes(vault)
    (vault / "foo.md").write_text("hi\n", encoding="utf-8")
    assert git.has_changes(vault)


def test_has_head_no_commits_yet(vault: pathlib.Path) -> None:
    """Fresh `git init` has no HEAD until the first commit lands."""
    assert not git.has_head(vault)


def test_has_head_after_commit(vault: pathlib.Path) -> None:
    (vault / "foo.md").write_text("hi\n", encoding="utf-8")
    git.commit_all(vault, "first")
    assert git.has_head(vault)


def test_commit_all_allow_empty_creates_commit(vault: pathlib.Path) -> None:
    """`om init` calls this to seed a HEAD even if the user has no notes."""
    assert not git.has_head(vault)
    git.commit_all_allow_empty(vault, "init")
    assert git.has_head(vault)


def test_has_remote_empty_then_added(vault: pathlib.Path) -> None:
    assert not git.has_remote(vault)
    git.add_remote(vault, "origin", "https://example.com/repo.git")
    assert git.has_remote(vault)


def test_unsynced_commit_count_no_head(vault: pathlib.Path) -> None:
    """Fresh repo with no HEAD → None (nothing to compare)."""
    assert git.unsynced_commit_count(vault) is None


def test_unsynced_commit_count_no_upstream(vault: pathlib.Path) -> None:
    """HEAD exists but no upstream tracking branch → None."""
    (vault / "foo.md").write_text("hi\n", encoding="utf-8")
    git.commit_all(vault, "first")
    assert git.unsynced_commit_count(vault) is None
