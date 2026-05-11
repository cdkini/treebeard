"""Interactive input layer for the chat REPL.

Wraps `prompt_toolkit`'s `PromptSession` for the TTY case (tab completion
on `/` commands, in-session history) and falls back to plain `input()`
when stdin isn't a TTY — the path `CliRunner` and shell pipes take.
"""

from __future__ import annotations

import sys
from collections.abc import Iterable
from typing import Any

from prompt_toolkit import PromptSession
from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.document import Document
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.history import InMemoryHistory

from treebeard.chat.slash import SLASH_HANDLERS
from treebeard.ui import status_console

# Glyph for the user-input prompt — matches `tb init`'s aesthetic.
PROMPT_GLYPH = "▸"


class SlashCompleter(Completer):
    """Tab-complete `/` commands from `SLASH_HANDLERS`.

    Triggers only when the buffer starts with `/` so normal prose
    doesn't get peppered with menu popups. The completion list is
    sourced from the live handler dict (filtered to slash forms — the
    bare `exit`/`quit` aliases are usability fallbacks, not something
    we want to suggest in a menu).
    """

    def __init__(self, commands: Iterable[str]) -> None:
        self._commands = sorted(c for c in commands if c.startswith("/"))

    def get_completions(self, document: Document, complete_event: Any) -> Iterable[Completion]:
        del complete_event
        text = document.text_before_cursor
        if not text.startswith("/"):
            return
        for cmd in self._commands:
            if cmd.startswith(text):
                yield Completion(cmd, start_position=-len(text))


def build_prompt_session() -> PromptSession[str] | None:
    """Construct a `PromptSession` for interactive stdin, or `None`
    when stdin isn't a TTY.

    Returning `None` triggers the `input()` fallback in `read_line` —
    the path tests rely on. `prompt_toolkit` would otherwise try to
    open `/dev/tty` directly and fight with `CliRunner`'s pipe.
    """
    if not sys.stdin.isatty():
        return None
    return PromptSession(
        completer=SlashCompleter(SLASH_HANDLERS.keys()),
        history=InMemoryHistory(),
        complete_while_typing=False,
    )


def read_line(session: PromptSession[str] | None) -> str:
    """Blocking stdin read — runs in a thread so it doesn't block the
    event loop. Uses `prompt_toolkit` when interactive (tab completion
    on `/` commands, history within the session); falls back to plain
    `input()` when `session is None` (non-TTY: pipes, tests).

    The `prompt_toolkit` path renders the glyph itself via the prompt
    HTML; the fallback pre-prints the glyph to stderr so it shows up
    even when stdout is piped.
    """
    if session is None:
        status_console.print(f"[bold cyan]{PROMPT_GLYPH}[/bold cyan] ", end="")
        return input()
    return session.prompt(HTML(f"<ansibrightcyan><b>{PROMPT_GLYPH}</b></ansibrightcyan> "))
