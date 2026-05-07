"""Tests for `om init`."""

from __future__ import annotations

import pathlib
import shutil
import subprocess
import tomllib

import pytest
from click.testing import CliRunner

from om.cli import cli


def _read_toml(path: pathlib.Path) -> dict[str, object]:
    with path.open("rb") as fh:
        return dict(tomllib.load(fh))


def _read_git_config(vault: pathlib.Path, key: str) -> str:
    return subprocess.run(
        ["git", "config", "--get", key],
        cwd=vault,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


@pytest.fixture(autouse=True)
def _stable_editor_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin `shutil.which` so the editor prompt's default doesn't depend
    on what's installed on the host running the tests."""
    monkeypatch.setattr(shutil, "which", lambda name: f"/usr/bin/{name}")


# Inputs to the init prompt sequence: vault path, editor, email, name, remote.
# The two identity prompts are blank-Entered so they accept the global default
# from `_isolated_git_global`. Remote is blank to skip.
_DEFAULT_TAIL = "vim\n\n\n\n"


def test_happy_path(runner: CliRunner, tmp_path: pathlib.Path) -> None:
    vault = tmp_path / "vault"
    cfg_dir = tmp_path / "cfg"

    result = runner.invoke(
        cli,
        ["init", "--config-dir", str(cfg_dir)],
        input=f"{vault}\n{_DEFAULT_TAIL}",
    )

    assert result.exit_code == 0, result.output
    assert (vault / ".om").is_dir()
    assert (vault / ".git").is_dir()
    assert f"Initialized vault at {vault}" in result.output
    assert f"Wrote config to {cfg_dir / 'config.toml'}" in result.output

    data = _read_toml(cfg_dir / "config.toml")
    assert data == {"vault": str(vault), "editor": "vim"}


def test_persists_chosen_editor(runner: CliRunner, tmp_path: pathlib.Path) -> None:
    vault = tmp_path / "vault"
    cfg_dir = tmp_path / "cfg"

    result = runner.invoke(
        cli,
        ["init", "--config-dir", str(cfg_dir)],
        input=f"{vault}\nnvim\n\n\n\n",
    )

    assert result.exit_code == 0, result.output
    data = _read_toml(cfg_dir / "config.toml")
    assert data["editor"] == "nvim"


def test_creates_missing_parents(runner: CliRunner, tmp_path: pathlib.Path) -> None:
    nested = tmp_path / "a" / "b" / "c" / "vault"
    cfg_dir = tmp_path / "cfg"

    result = runner.invoke(
        cli,
        ["init", "--config-dir", str(cfg_dir)],
        input=f"{nested}\n{_DEFAULT_TAIL}",
    )

    assert result.exit_code == 0, result.output
    assert (nested / ".om").is_dir()
    assert (nested / ".git").is_dir()


def test_accepts_empty_existing_dir(runner: CliRunner, tmp_path: pathlib.Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    cfg_dir = tmp_path / "cfg"

    result = runner.invoke(
        cli,
        ["init", "--config-dir", str(cfg_dir)],
        input=f"{vault}\n{_DEFAULT_TAIL}",
    )

    assert result.exit_code == 0, result.output
    assert (vault / ".om").is_dir()
    assert (vault / ".git").is_dir()
    assert f"Initialized vault at {vault}" in result.output


def test_adopts_existing_full_vault(runner: CliRunner, tmp_path: pathlib.Path) -> None:
    vault = tmp_path / "vault"
    (vault / ".om").mkdir(parents=True)
    subprocess.run(["git", "init", "--quiet"], cwd=vault, check=True)
    (vault / "note.md").write_text("preexisting\n", encoding="utf-8")
    cfg_dir = tmp_path / "cfg"

    result = runner.invoke(
        cli,
        ["init", "--config-dir", str(cfg_dir)],
        input=f"{vault}\n{_DEFAULT_TAIL}",
    )

    assert result.exit_code == 0, result.output
    assert f"Adopted existing vault at {vault}" in result.output
    assert (vault / "note.md").read_text(encoding="utf-8") == "preexisting\n"


def test_rejects_om_without_git(runner: CliRunner, tmp_path: pathlib.Path) -> None:
    half = tmp_path / "half"
    (half / ".om").mkdir(parents=True)
    good = tmp_path / "good"
    cfg_dir = tmp_path / "cfg"

    result = runner.invoke(
        cli,
        ["init", "--config-dir", str(cfg_dir)],
        input=f"{half}\n{good}\n{_DEFAULT_TAIL}",
    )

    assert result.exit_code == 0, result.output
    assert f"{half} has .om/ but no .git/" in result.output
    assert (good / ".om").is_dir()


def test_rejects_non_empty_non_vault_dir(runner: CliRunner, tmp_path: pathlib.Path) -> None:
    messy = tmp_path / "messy"
    messy.mkdir()
    (messy / "random.txt").write_text("hi", encoding="utf-8")
    good = tmp_path / "good"
    cfg_dir = tmp_path / "cfg"

    result = runner.invoke(
        cli,
        ["init", "--config-dir", str(cfg_dir)],
        input=f"{messy}\n{good}\n{_DEFAULT_TAIL}",
    )

    assert result.exit_code == 0, result.output
    assert "is not empty and is not an om vault" in result.output
    assert (good / ".om").is_dir()


def test_rejects_when_path_is_a_file(runner: CliRunner, tmp_path: pathlib.Path) -> None:
    notvault = tmp_path / "notvault"
    notvault.write_text("oops", encoding="utf-8")
    good = tmp_path / "good"
    cfg_dir = tmp_path / "cfg"

    result = runner.invoke(
        cli,
        ["init", "--config-dir", str(cfg_dir)],
        input=f"{notvault}\n{good}\n{_DEFAULT_TAIL}",
    )

    assert result.exit_code == 0, result.output
    assert f"{notvault} is not a directory" in result.output
    assert (good / ".om").is_dir()


def test_rejects_invalid_editor_then_accepts(runner: CliRunner, tmp_path: pathlib.Path) -> None:
    vault = tmp_path / "vault"
    cfg_dir = tmp_path / "cfg"

    result = runner.invoke(
        cli,
        ["init", "--config-dir", str(cfg_dir)],
        input=f"{vault}\nemacs\nvim\n\n\n\n",
    )

    assert result.exit_code == 0, result.output
    data = _read_toml(cfg_dir / "config.toml")
    assert data["editor"] == "vim"


def test_refuses_to_overwrite_existing_config(runner: CliRunner, tmp_path: pathlib.Path) -> None:
    cfg_dir = tmp_path / "cfg"
    cfg_dir.mkdir()
    cfg_path = cfg_dir / "config.toml"
    cfg_path.write_text('vault = "/some/old/place"\neditor = "vim"\n', encoding="utf-8")

    vault = tmp_path / "vault"
    result = runner.invoke(
        cli,
        ["init", "--config-dir", str(cfg_dir)],
        input=f"{vault}\n{_DEFAULT_TAIL}",
    )

    assert result.exit_code != 0
    assert "already configured" in result.output
    assert not vault.exists()
    assert cfg_path.read_text() == 'vault = "/some/old/place"\neditor = "vim"\n'


def test_tilde_expansion_uses_home_env(
    runner: CliRunner, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))

    cfg_dir = tmp_path / "cfg"
    result = runner.invoke(
        cli,
        ["init", "--config-dir", str(cfg_dir)],
        input=f"~/vault\n{_DEFAULT_TAIL}",
    )

    assert result.exit_code == 0, result.output
    assert (fake_home / "vault" / ".om").is_dir()
    data = _read_toml(cfg_dir / "config.toml")
    assert data == {"vault": str(fake_home / "vault"), "editor": "vim"}


def test_relative_path_is_stored_absolute(
    runner: CliRunner, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    cfg_dir = tmp_path / "cfg"

    result = runner.invoke(
        cli,
        ["init", "--config-dir", str(cfg_dir)],
        input=f"vault\n{_DEFAULT_TAIL}",
    )

    assert result.exit_code == 0, result.output
    data = _read_toml(cfg_dir / "config.toml")
    stored = data["vault"]
    assert isinstance(stored, str)
    assert pathlib.Path(stored).is_absolute()
    assert pathlib.Path(stored) == (tmp_path / "vault").resolve()


def test_writes_git_identity_to_repo(runner: CliRunner, tmp_path: pathlib.Path) -> None:
    vault = tmp_path / "vault"
    cfg_dir = tmp_path / "cfg"

    result = runner.invoke(
        cli,
        ["init", "--config-dir", str(cfg_dir)],
        input=f"{vault}\nvim\nme@example.com\nMe\n\n",
    )

    assert result.exit_code == 0, result.output
    assert _read_git_config(vault, "user.email") == "me@example.com"
    assert _read_git_config(vault, "user.name") == "Me"


def test_adds_remote_when_url_provided(runner: CliRunner, tmp_path: pathlib.Path) -> None:
    vault = tmp_path / "vault"
    cfg_dir = tmp_path / "cfg"
    remote_url = "git@example.com:me/notes.git"

    result = runner.invoke(
        cli,
        ["init", "--config-dir", str(cfg_dir)],
        input=f"{vault}\nvim\n\n\n{remote_url}\n",
    )

    assert result.exit_code == 0, result.output
    assert _read_git_config(vault, "remote.origin.url") == remote_url


def test_skips_remote_when_blank(runner: CliRunner, tmp_path: pathlib.Path) -> None:
    vault = tmp_path / "vault"
    cfg_dir = tmp_path / "cfg"

    result = runner.invoke(
        cli,
        ["init", "--config-dir", str(cfg_dir)],
        input=f"{vault}\n{_DEFAULT_TAIL}",
    )

    assert result.exit_code == 0, result.output
    remotes = subprocess.run(
        ["git", "remote"], cwd=vault, check=True, capture_output=True, text=True
    ).stdout
    assert remotes.strip() == ""
