"""Git helpers used by `om init`, the CLI auto-commit hook, and `om sync`.

Every om vault is a git repo: scaffolding `om init` runs `git init`, every
mutating command auto-commits via the CLI hook, and `om sync` pushes/pulls.
This module wraps the subprocess calls so callers don't shell out directly.
"""

from __future__ import annotations

import pathlib
import subprocess


def ensure_initialized(vault: pathlib.Path) -> None:
    """Run `git init` in `vault` if it isn't already a repo."""
    if (vault / ".git").is_dir():
        return
    subprocess.run(["git", "init", "--quiet"], cwd=vault, check=True)


def get_config(vault: pathlib.Path, key: str) -> str | None:
    """Return the value of a git config `key` (repo-then-global), or None."""
    result = subprocess.run(
        ["git", "config", "--get", key],
        cwd=vault,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return None
    value = result.stdout.strip()
    return value or None


def set_config(vault: pathlib.Path, key: str, value: str) -> None:
    """Write `key = value` into the repo's `.git/config`."""
    subprocess.run(["git", "config", key, value], cwd=vault, check=True)


def add_remote(vault: pathlib.Path, name: str, url: str) -> None:
    """`git remote add <name> <url>`."""
    subprocess.run(["git", "remote", "add", name, url], cwd=vault, check=True)


def has_changes(vault: pathlib.Path) -> bool:
    """True if the working tree or index has anything uncommitted."""
    out = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=vault,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    return bool(out.strip())


def changed_root_md_paths(vault: pathlib.Path) -> list[pathlib.Path]:
    """Return absolute paths of root-level `.md` files that differ from HEAD.

    Used by the CLI close hook to find every note touched during a
    subcommand — including side-jumps to files the command never
    explicitly opened. Wraps `git status --porcelain -z
    --untracked-files=all`:

      - `-z`: NUL-delimited records, no quoting on weird filenames.
      - `--untracked-files=all`: catches notes created during the
        subcommand (`:w newfile.md` inside vim).
      - For renames (`R`) and copies (`C`), porcelain emits two NUL
        records per entry — `<new>\\0<old>\\0` — and we use the new path.

    Filters to: paths ending in `.md`, no `/` separator (vault is flat —
    `.om/`, `.git/`, and any subdir are excluded by construction), and
    paths that exist on disk after the subcommand exits (deletions are
    skipped — nothing to reconcile on a missing file).
    """
    out = subprocess.run(
        ["git", "status", "--porcelain", "-z", "--untracked-files=all"],
        cwd=vault,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    paths: list[pathlib.Path] = []
    records = out.split("\0")
    i = 0
    while i < len(records):
        record = records[i]
        i += 1
        if not record:
            continue
        # Each record is "XY<space><path>". The first two chars are the
        # status; column 3 is a space; the rest is the path.
        if len(record) < 4:
            continue
        status = record[:2]
        name = record[3:]
        # R/C entries are followed by a separate record holding the old
        # path; consume and ignore it.
        if status[0] in ("R", "C") or status[1] in ("R", "C"):
            i += 1
        if "/" in name:
            continue
        if not name.endswith(".md"):
            continue
        candidate = vault / name
        if not candidate.exists():
            continue
        paths.append(candidate)
    return paths


def commit_all(vault: pathlib.Path, message: str) -> None:
    """Stage everything and commit with `message`."""
    subprocess.run(["git", "add", "-A"], cwd=vault, check=True)
    subprocess.run(
        ["git", "commit", "--quiet", "-m", message],
        cwd=vault,
        check=True,
    )


def commit_all_allow_empty(vault: pathlib.Path, message: str) -> None:
    """Like `commit_all`, but creates the commit even when nothing is
    staged. Used by `om init` to guarantee a fresh vault has a HEAD even
    if the user hasn't created any notes yet."""
    subprocess.run(["git", "add", "-A"], cwd=vault, check=True)
    subprocess.run(
        ["git", "commit", "--quiet", "--allow-empty", "-m", message],
        cwd=vault,
        check=True,
    )


def has_head(vault: pathlib.Path) -> bool:
    """True if the repo has at least one commit (HEAD resolves)."""
    result = subprocess.run(
        ["git", "rev-parse", "--verify", "--quiet", "HEAD"],
        cwd=vault,
        capture_output=True,
    )
    return result.returncode == 0


def has_remote(vault: pathlib.Path) -> bool:
    """True if at least one git remote is configured."""
    out = subprocess.run(
        ["git", "remote"],
        cwd=vault,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    return bool(out.strip())


def pull_rebase_push(vault: pathlib.Path) -> None:
    """`git pull --rebase` then `git push`. Raises on either failure."""
    subprocess.run(["git", "pull", "--rebase"], cwd=vault, check=True)
    subprocess.run(["git", "push"], cwd=vault, check=True)


def unsynced_commit_count(vault: pathlib.Path) -> int | None:
    """Count commits on HEAD that are not on its upstream.

    Returns None when there's nothing meaningful to compare against — no
    HEAD yet, no upstream tracking branch configured, or the upstream
    ref is missing locally (e.g. the user has never fetched). Returns 0
    when HEAD is in sync with upstream, or a positive int otherwise.

    Deliberately does not run `git fetch` — the caller is the post-command
    hook, which must be silent and offline-safe.
    """
    if not has_head(vault):
        return None
    result = subprocess.run(
        ["git", "rev-list", "--count", "@{u}..HEAD"],
        cwd=vault,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return None
    out = result.stdout.strip()
    return int(out) if out.isdigit() else None
