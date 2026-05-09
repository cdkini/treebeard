"""Unit tests for `treebeard.fzf` — the shared fzf argv prefix."""

from __future__ import annotations

from treebeard import fzf


def test_base_args_without_header() -> None:
    """No `--header` flag when header is None — fzf shows the prompt only."""
    args = fzf.base_args("note> ")
    assert args == ["fzf", "--prompt=note> "]


def test_base_args_with_header() -> None:
    args = fzf.base_args("note> ", header="Tab to multi-select")
    assert args == ["fzf", "--prompt=note> ", "--header=Tab to multi-select"]


def test_cancelled_returncode_is_130() -> None:
    """fzf's documented exit code for Esc/Ctrl-C; pickers branch on this."""
    assert fzf.CANCELLED_RETURNCODE == 130
