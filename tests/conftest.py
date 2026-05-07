"""Shared pytest fixtures."""

from __future__ import annotations

import pytest
from click.testing import CliRunner


@pytest.fixture
def runner() -> CliRunner:
    """Click test runner for invoking the CLI in-process."""
    return CliRunner()
