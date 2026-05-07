"""Tests for `om init`."""

from __future__ import annotations

import pathlib
import shutil
import tomllib

import pytest
from click.testing import CliRunner

from om.cli import cli


def _read_toml(path: pathlib.Path) -> dict[str, object]:
    with path.open("rb") as fh:
        return dict(tomllib.load(fh))


@pytest.fixture(autouse=True)
def _stable_editor_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin `shutil.which` so the editor prompt's default doesn't depend
    on what's installed on the host running the tests."""
    monkeypatch.setattr(shutil, "which", lambda name: f"/usr/bin/{name}")


def test_happy_path(runner: CliRunner, tmp_path: pathlib.Path) -> None:
    vault = tmp_path / "vault"
    cfg_dir = tmp_path / "cfg"

    result = runner.invoke(
        cli,
        ["init", "--config-dir", str(cfg_dir)],
        input=f"{vault}\nvim\n",
    )

    assert result.exit_code == 0, result.output
    assert (vault / ".om").is_dir()
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
        input=f"{vault}\nnvim\n",
    )

    assert result.exit_code == 0, result.output
    data = _read_toml(cfg_dir / "config.toml")
    assert data["editor"] == "nvim"


def test_reprompts_when_path_already_exists(runner: CliRunner, tmp_path: pathlib.Path) -> None:
    taken = tmp_path / "taken"
    taken.mkdir()
    good = tmp_path / "good"
    cfg_dir = tmp_path / "cfg"

    result = runner.invoke(
        cli,
        ["init", "--config-dir", str(cfg_dir)],
        input=f"{taken}\n{good}\nvim\n",
    )

    assert result.exit_code == 0, result.output
    assert f"{taken} already exists" in result.output
    assert (good / ".om").is_dir()


def test_reprompts_when_parent_missing(runner: CliRunner, tmp_path: pathlib.Path) -> None:
    bad = tmp_path / "no" / "such" / "parent" / "vault"
    good = tmp_path / "good"
    cfg_dir = tmp_path / "cfg"

    result = runner.invoke(
        cli,
        ["init", "--config-dir", str(cfg_dir)],
        input=f"{bad}\n{good}\nvim\n",
    )

    assert result.exit_code == 0, result.output
    assert "does not exist" in result.output
    assert (good / ".om").is_dir()


def test_rejects_invalid_editor_then_accepts(runner: CliRunner, tmp_path: pathlib.Path) -> None:
    vault = tmp_path / "vault"
    cfg_dir = tmp_path / "cfg"

    result = runner.invoke(
        cli,
        ["init", "--config-dir", str(cfg_dir)],
        input=f"{vault}\nemacs\nvim\n",
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
        input=f"{vault}\nvim\n",
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
        input="~/vault\nvim\n",
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
        input="vault\nvim\n",
    )

    assert result.exit_code == 0, result.output
    data = _read_toml(cfg_dir / "config.toml")
    stored = data["vault"]
    assert isinstance(stored, str)
    assert pathlib.Path(stored).is_absolute()
    assert pathlib.Path(stored) == (tmp_path / "vault").resolve()
