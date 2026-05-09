"""Tests for `treebeard init`."""

from __future__ import annotations

import pathlib
import shutil
import subprocess
import tomllib

import pytest
from click.testing import CliRunner

from treebeard.cli import cli


def _read_toml(path: pathlib.Path) -> dict[str, object]:
    with path.open("rb") as fh:
        return dict(tomllib.load(fh))


@pytest.fixture(autouse=True)
def _stable_dependency_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin `shutil.which` so the auto-picked editor/previewer defaults
    don't depend on what's installed on the host running the tests."""
    monkeypatch.setattr(shutil, "which", lambda name: f"/usr/bin/{name}")


def test_happy_path(runner: CliRunner, tmp_path: pathlib.Path, cfg_dir: pathlib.Path) -> None:
    vault = tmp_path / "vault"

    result = runner.invoke(cli, ["init", str(vault)])

    assert result.exit_code == 0, result.output
    assert (vault / ".treebeard").is_dir()
    assert (vault / ".git").is_dir()
    assert f"Initialized vault at {vault}" in result.output
    assert f"Wrote config to {cfg_dir / 'config.toml'}" in result.output

    data = _read_toml(cfg_dir / "config.toml")
    # Editor/previewer pick the first available from the dependency
    # registry (`nvim`, `bat`); `_stable_dependency_defaults` pins
    # `shutil.which` to make both look installed.
    assert data == {
        "vault": {"path": str(vault)},
        "editor": {"command": "nvim", "previewer": "bat"},
        "chat": {"model": "sonnet"},
        "sync": {"warn_threshold": 10},
        "secrets": {"granola_api_key": ""},
    }


def test_falls_back_to_constants_when_nothing_installed(
    runner: CliRunner,
    tmp_path: pathlib.Path,
    cfg_dir: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When no editor/previewer is on PATH, the config.toml falls back
    to the hardcoded `DEFAULT_EDITOR`/`DEFAULT_PREVIEWER` constants.

    Patches `first_available` directly rather than `shutil.which` so the
    CLI startup dependency check (which also calls `which`) doesn't
    spuriously fail looking for git/fzf/rg.
    """
    from treebeard import dependencies

    monkeypatch.setattr(dependencies, "first_available", lambda _deps: None)
    vault = tmp_path / "vault"

    result = runner.invoke(cli, ["init", str(vault)])

    assert result.exit_code == 0, result.output
    data = _read_toml(cfg_dir / "config.toml")
    editor_section = data["editor"]
    assert isinstance(editor_section, dict)
    assert editor_section["command"] == "vim"
    assert editor_section["previewer"] == "bat"


def test_creates_missing_parents(runner: CliRunner, tmp_path: pathlib.Path) -> None:
    nested = tmp_path / "a" / "b" / "c" / "vault"

    result = runner.invoke(cli, ["init", str(nested)])

    assert result.exit_code == 0, result.output
    assert (nested / ".treebeard").is_dir()
    assert (nested / ".git").is_dir()


def test_accepts_empty_existing_dir(runner: CliRunner, tmp_path: pathlib.Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()

    result = runner.invoke(cli, ["init", str(vault)])

    assert result.exit_code == 0, result.output
    assert (vault / ".treebeard").is_dir()
    assert (vault / ".git").is_dir()
    assert f"Initialized vault at {vault}" in result.output


def test_adopts_existing_full_vault(runner: CliRunner, tmp_path: pathlib.Path) -> None:
    vault = tmp_path / "vault"
    (vault / ".treebeard").mkdir(parents=True)
    subprocess.run(["git", "init", "--quiet"], cwd=vault, check=True)
    (vault / "note.md").write_text("preexisting\n", encoding="utf-8")

    result = runner.invoke(cli, ["init", str(vault)])

    assert result.exit_code == 0, result.output
    assert f"Adopted existing vault at {vault}" in result.output
    assert (vault / "note.md").read_text(encoding="utf-8") == "preexisting\n"


def test_rejects_om_without_git(runner: CliRunner, tmp_path: pathlib.Path) -> None:
    half = tmp_path / "half"
    (half / ".treebeard").mkdir(parents=True)

    result = runner.invoke(cli, ["init", str(half)])

    assert result.exit_code != 0
    assert f"{half} has .treebeard/ but no .git/" in result.output


def test_rejects_non_empty_non_vault_dir(runner: CliRunner, tmp_path: pathlib.Path) -> None:
    messy = tmp_path / "messy"
    messy.mkdir()
    (messy / "random.txt").write_text("hi", encoding="utf-8")

    result = runner.invoke(cli, ["init", str(messy)])

    assert result.exit_code != 0
    assert "is not empty and is not a treebeard vault" in result.output


def test_rejects_when_path_is_a_file(runner: CliRunner, tmp_path: pathlib.Path) -> None:
    notvault = tmp_path / "notvault"
    notvault.write_text("oops", encoding="utf-8")

    result = runner.invoke(cli, ["init", str(notvault)])

    assert result.exit_code != 0
    assert f"{notvault} is not a directory" in result.output


def test_refuses_to_overwrite_existing_config(
    runner: CliRunner, tmp_path: pathlib.Path, cfg_dir: pathlib.Path
) -> None:
    cfg_path = cfg_dir / "config.toml"
    legacy = '[vault]\npath = "/some/old/place"\n\n[editor]\ncommand = "vim"\n'
    cfg_path.write_text(legacy, encoding="utf-8")

    vault = tmp_path / "vault"
    result = runner.invoke(cli, ["init", str(vault)])

    assert result.exit_code != 0
    assert "already configured" in result.output
    assert not vault.exists()
    assert cfg_path.read_text() == legacy


def test_tilde_expansion_uses_home_env(
    runner: CliRunner,
    tmp_path: pathlib.Path,
    cfg_dir: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))

    result = runner.invoke(cli, ["init", "~/vault"])

    assert result.exit_code == 0, result.output
    assert (fake_home / "vault" / ".treebeard").is_dir()
    data = _read_toml(cfg_dir / "config.toml")
    vault_section = data["vault"]
    assert isinstance(vault_section, dict)
    assert vault_section["path"] == str(fake_home / "vault")


def test_relative_path_is_stored_absolute(
    runner: CliRunner,
    tmp_path: pathlib.Path,
    cfg_dir: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)

    # Use `./vault` rather than bare `vault`: validation rejects bare
    # tokens with no separator to catch typos like `asdfasdf`.
    result = runner.invoke(cli, ["init", "./vault"])

    assert result.exit_code == 0, result.output
    data = _read_toml(cfg_dir / "config.toml")
    vault_section = data["vault"]
    assert isinstance(vault_section, dict)
    stored = vault_section["path"]
    assert isinstance(stored, str)
    assert pathlib.Path(stored).is_absolute()
    assert pathlib.Path(stored) == (tmp_path / "vault").resolve()


def test_rejects_bare_token(
    runner: CliRunner, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Bare `asdfasdf` (no `/` and no `~`) is almost always a typo —
    surface the error instead of silently treating it as a relative path."""
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(cli, ["init", "asdfasdf"])

    assert result.exit_code != 0
    assert "doesn't look like a path" in result.output


def test_inherits_global_git_identity(runner: CliRunner, tmp_path: pathlib.Path) -> None:
    """Init no longer prompts for git identity; commits should still
    succeed using whatever the global git config provides (set up by
    the `_isolated_git_global` autouse fixture)."""
    vault = tmp_path / "vault"

    result = runner.invoke(cli, ["init", str(vault)])

    assert result.exit_code == 0, result.output
    log = subprocess.run(
        ["git", "log", "-1", "--format=%an <%ae>"],
        cwd=vault,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert log == "Test User <test@example.com>"


def _commit_count(vault: pathlib.Path) -> int:
    out = subprocess.run(
        ["git", "rev-list", "--count", "--all"],
        cwd=vault,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    return int(out) if out else 0


def _head_message(vault: pathlib.Path) -> str:
    return subprocess.run(
        ["git", "log", "-1", "--format=%s"],
        cwd=vault,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def test_creates_initial_commit_on_fresh_vault(runner: CliRunner, tmp_path: pathlib.Path) -> None:
    """A fresh vault with no notes still gets a HEAD so downstream
    commands (sync, the auto-commit hook) don't trip on a commitless repo."""
    vault = tmp_path / "vault"

    result = runner.invoke(cli, ["init", str(vault)])

    assert result.exit_code == 0, result.output
    assert _commit_count(vault) == 1
    assert _head_message(vault).startswith("init: ")


def test_adopts_uncommitted_files_into_initial_commit(
    runner: CliRunner, tmp_path: pathlib.Path
) -> None:
    """When adopting a vault whose git repo has no commits but has
    uncommitted files, the bootstrap commit picks them up."""
    vault = tmp_path / "vault"
    (vault / ".treebeard").mkdir(parents=True)
    subprocess.run(["git", "init", "--quiet"], cwd=vault, check=True)
    (vault / "note.md").write_text("preexisting\n", encoding="utf-8")

    result = runner.invoke(cli, ["init", str(vault)])

    assert result.exit_code == 0, result.output
    assert _commit_count(vault) == 1
    head_files = (
        subprocess.run(
            ["git", "show", "--name-only", "--format=", "HEAD"],
            cwd=vault,
            check=True,
            capture_output=True,
            text=True,
        )
        .stdout.strip()
        .splitlines()
    )
    assert "note.md" in head_files


def test_does_not_recommit_when_adopting_repo_with_history(
    runner: CliRunner, tmp_path: pathlib.Path
) -> None:
    """Adopting a vault that already has commits must not add an empty
    `init:` commit — that would pollute the user's history."""
    vault = tmp_path / "vault"
    (vault / ".treebeard").mkdir(parents=True)
    subprocess.run(["git", "init", "--quiet", "-b", "main"], cwd=vault, check=True)
    subprocess.run(["git", "config", "user.email", "x@y"], cwd=vault, check=True)
    subprocess.run(["git", "config", "user.name", "X"], cwd=vault, check=True)
    (vault / "note.md").write_text("preexisting\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=vault, check=True)
    subprocess.run(["git", "commit", "--quiet", "-m", "preexisting"], cwd=vault, check=True)

    result = runner.invoke(cli, ["init", str(vault)])

    assert result.exit_code == 0, result.output
    # The point of this test is that init itself doesn't add an empty
    # bootstrap commit, so HEAD must still be the user's `preexisting`
    # commit.
    assert _commit_count(vault) == 1
    assert _head_message(vault) == "preexisting"
