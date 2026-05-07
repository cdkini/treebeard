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


def commit_all(vault: pathlib.Path, message: str) -> None:
    """Stage everything and commit with `message`."""
    subprocess.run(["git", "add", "-A"], cwd=vault, check=True)
    subprocess.run(
        ["git", "commit", "--quiet", "-m", message],
        cwd=vault,
        check=True,
    )


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
