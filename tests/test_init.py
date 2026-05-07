"""Tests for `om init`."""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest
from click.testing import CliRunner

from om.cli import cli


def _read_toml(path: Path) -> dict[str, object]:
    with path.open("rb") as fh:
        return dict(tomllib.load(fh))


def test_happy_path(runner: CliRunner, tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    cfg_dir = tmp_path / "cfg"

    result = runner.invoke(cli, ["init", "--config-dir", str(cfg_dir), str(vault)])

    assert result.exit_code == 0, result.output
    assert (vault / ".om-vault").is_file()
    assert f"Initialized vault at {vault}" in result.output
    assert f"Wrote config to {cfg_dir / 'config.toml'}" in result.output

    data = _read_toml(cfg_dir / "config.toml")
    assert data == {"vault": str(vault)}


def test_errors_when_target_is_existing_file(runner: CliRunner, tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.write_text("hi")
    cfg_dir = tmp_path / "cfg"

    result = runner.invoke(cli, ["init", "--config-dir", str(cfg_dir), str(vault)])

    assert result.exit_code != 0
    assert "already exists" in result.output
    assert not (cfg_dir / "config.toml").exists()


def test_errors_when_target_is_nonempty_dir(runner: CliRunner, tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "stuff.txt").write_text("hi")
    cfg_dir = tmp_path / "cfg"

    result = runner.invoke(cli, ["init", "--config-dir", str(cfg_dir), str(vault)])

    assert result.exit_code != 0
    assert "already exists" in result.output
    assert not (cfg_dir / "config.toml").exists()


def test_errors_when_target_is_empty_dir(runner: CliRunner, tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    cfg_dir = tmp_path / "cfg"

    result = runner.invoke(cli, ["init", "--config-dir", str(cfg_dir), str(vault)])

    assert result.exit_code != 0
    assert "already exists" in result.output
    assert not (cfg_dir / "config.toml").exists()


def test_errors_when_parent_missing(runner: CliRunner, tmp_path: Path) -> None:
    vault = tmp_path / "no" / "such" / "parent" / "vault"
    cfg_dir = tmp_path / "cfg"

    result = runner.invoke(cli, ["init", "--config-dir", str(cfg_dir), str(vault)])

    assert result.exit_code != 0
    assert "parent directory" in result.output
    assert "does not exist" in result.output


def test_reinit_errors_when_vault_already_set(runner: CliRunner, tmp_path: Path) -> None:
    cfg_dir = tmp_path / "cfg"
    cfg_dir.mkdir()
    cfg_path = cfg_dir / "config.toml"
    cfg_path.write_text('vault = "/some/old/place"\n')

    vault = tmp_path / "vault"
    result = runner.invoke(cli, ["init", "--config-dir", str(cfg_dir), str(vault)])

    assert result.exit_code != 0
    assert "already has a vault configured" in result.output
    assert not vault.exists()
    # Original config untouched.
    assert cfg_path.read_text() == 'vault = "/some/old/place"\n'


def test_reinit_succeeds_when_other_keys_present(runner: CliRunner, tmp_path: Path) -> None:
    cfg_dir = tmp_path / "cfg"
    cfg_dir.mkdir()
    cfg_path = cfg_dir / "config.toml"
    cfg_path.write_text('theme = "dark"\n')

    vault = tmp_path / "vault"
    result = runner.invoke(cli, ["init", "--config-dir", str(cfg_dir), str(vault)])

    assert result.exit_code == 0, result.output
    data = _read_toml(cfg_path)
    assert data == {"theme": "dark", "vault": str(vault)}


def test_tilde_expansion_uses_home_env(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))

    cfg_dir = tmp_path / "cfg"
    result = runner.invoke(cli, ["init", "--config-dir", str(cfg_dir), "~/vault"])

    assert result.exit_code == 0, result.output
    assert (fake_home / "vault" / ".om-vault").is_file()
    data = _read_toml(cfg_dir / "config.toml")
    assert data == {"vault": str(fake_home / "vault")}


def test_relative_path_is_stored_absolute(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    cfg_dir = tmp_path / "cfg"

    result = runner.invoke(cli, ["init", "--config-dir", str(cfg_dir), "vault"])

    assert result.exit_code == 0, result.output
    data = _read_toml(cfg_dir / "config.toml")
    stored = data["vault"]
    assert isinstance(stored, str)
    assert Path(stored).is_absolute()
    assert Path(stored) == (tmp_path / "vault").resolve()
