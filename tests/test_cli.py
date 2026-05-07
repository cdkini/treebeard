"""Smoke tests for the `om` CLI entry point."""

from __future__ import annotations

from click.testing import CliRunner

from om import __version__
from om.cli import cli


def test_help(runner: CliRunner) -> None:
    result = runner.invoke(cli, ["--help"])
    assert result.exit_code == 0
    assert "om — the omniscience CLI." in result.output


def test_version(runner: CliRunner) -> None:
    result = runner.invoke(cli, ["--version"])
    assert result.exit_code == 0
    assert __version__ in result.output
