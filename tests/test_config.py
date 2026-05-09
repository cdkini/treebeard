"""Tests for `om config`."""

from __future__ import annotations

import pathlib

from click.testing import CliRunner

from om.cli import cli
from om.config import config_path_for
from tests.conftest import EditorFake, write_cfg


def test_opens_config_file(
    runner: CliRunner,
    cfg_dir: pathlib.Path,
    vault: pathlib.Path,
    fake_editor: list[EditorFake],
) -> None:
    write_cfg(cfg_dir, vault)
    captured: dict[str, object] = {}

    def record(editor: str, path: pathlib.Path) -> None:
        captured["editor"] = editor
        captured["path"] = path

    fake_editor.append(record)

    result = runner.invoke(cli, ["config"])

    assert result.exit_code == 0, result.output
    assert captured["editor"] == "vim"
    assert captured["path"] == config_path_for(str(cfg_dir))
    assert str(config_path_for(str(cfg_dir))) in result.output


def test_errors_when_not_initialized(runner: CliRunner, cfg_dir: pathlib.Path) -> None:
    result = runner.invoke(cli, ["config"])

    assert result.exit_code != 0
    assert "no vault configured" in result.output
