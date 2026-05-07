"""Tests for `om help` — equivalence with `om --help` and picker invisibility."""

from __future__ import annotations

from click.testing import CliRunner

from om.cli import cli


def test_om_help_matches_dash_help(runner: CliRunner) -> None:
    a = runner.invoke(cli, ["help"])
    b = runner.invoke(cli, ["--help"])
    assert a.exit_code == 0, a.output
    assert b.exit_code == 0, b.output
    assert a.output.strip() == b.output.strip()


def test_help_lists_known_commands(runner: CliRunner) -> None:
    result = runner.invoke(cli, ["help"])
    assert "note" in result.output
    assert "daily" in result.output
    assert "sync" in result.output
    assert "config" in result.output
    assert "help" in result.output
    assert "find" in result.output
