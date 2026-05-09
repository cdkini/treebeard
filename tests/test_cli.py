"""Smoke tests for the `tb` CLI entry point."""

from __future__ import annotations

from click.testing import CliRunner

from treebeard import __version__
from treebeard.cli import cli


def test_help(runner: CliRunner) -> None:
    result = runner.invoke(cli, ["--help"])
    assert result.exit_code == 0
    assert "tb — a personal-notes CLI." in result.output


def test_version(runner: CliRunner) -> None:
    result = runner.invoke(cli, ["--version"])
    assert result.exit_code == 0
    assert __version__ in result.output
