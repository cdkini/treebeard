"""Shared fzf wiring used by `om find` and `om grep`."""

from __future__ import annotations

CANCELLED_RETURNCODE = 130


def base_args(prompt: str, *, header: str | None = None) -> list[str]:
    """Argv prefix shared by every om picker.

    Omitting `--height` makes fzf take the full alternate screen, which is
    what we want — the picker is the foreground task while it's open.
    """
    args = ["fzf", f"--prompt={prompt}"]
    if header is not None:
        args.append(f"--header={header}")
    return args
