"""`tb chat` — interactive Claude REPL with JSONL transcript."""

from __future__ import annotations

import click

from treebeard.config import load_config


@click.command("chat")
def command() -> None:
    """Start an interactive LLM chat session."""
    # Defer the chat import: it pulls in claude_agent_sdk + prompt_toolkit
    # (~200ms), which would otherwise land on every `tb` invocation via
    # command auto-registration. See CLAUDE.md "Startup performance".
    from treebeard import chat

    cfg = load_config()
    chat.run_repl(cfg.vault, cfg.chat_model)
