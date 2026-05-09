"""Terminal UI helpers — Rich-styled status, errors, and success messages.

Status output (errors, warnings, info, success) goes to stderr through a
shared Rich `Console`. Stderr keeps it off the pipe-safe stdout channel
that callers parse (e.g. `vim "$(tb note foo)"`).

Path output stays on stdout via `click.echo` — that's what existing code
already does, and Click's plain echo is lazy w.r.t. `sys.stdout`, which
plays nicely with `CliRunner`'s stream swap during tests.
"""

from __future__ import annotations

import click
from rich.console import Console

# Constructed once. Rich resolves `sys.stderr` at print time, so this
# survives CliRunner swapping stderr in tests. `soft_wrap=True` keeps
# long values (paths, error reasons) on one line — wrapping mid-path
# would break tests that assert path substrings and would hurt UX.
status_console = Console(stderr=True, highlight=False, soft_wrap=True)


def success(message: str) -> None:
    """Green ✓ + message."""
    status_console.print(f"[green]✓[/green] {message}")


def warn(message: str) -> None:
    """Yellow ⚠ + message."""
    status_console.print(f"[yellow]⚠[/yellow] {message}")


def info(message: str) -> None:
    """Dim message (no glyph)."""
    status_console.print(f"[dim]{message}[/dim]")


def error(message: str, *, hint: str | None = None) -> None:
    """Red ✗ + message; optional dim hint on the next line."""
    status_console.print(f"[red]✗[/red] {message}")
    if hint:
        status_console.print(f"  [dim]{hint}[/dim]")


def path(value: str) -> None:
    """Plain path on stdout — pipe-safe, no markup."""
    click.echo(value)


class TreebeardError(click.ClickException):
    """`ClickException` that renders via `ui.error` instead of "Error: ...".

    Same exit-code semantics as `ClickException` (1). Pass an optional
    `hint` to surface a follow-up suggestion in dim text (e.g. the exact
    command to run to fix the situation).
    """

    def __init__(self, message: str, *, hint: str | None = None) -> None:
        super().__init__(message)
        self.hint = hint

    def show(self, file: object | None = None) -> None:  # type: ignore[override]
        del file  # we always go through the shared stderr console
        error(self.format_message(), hint=self.hint)
