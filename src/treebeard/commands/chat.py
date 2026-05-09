"""`tb chat` — interactive Claude REPL with JSONL transcript."""

from __future__ import annotations

import click

from treebeard import chat
from treebeard.config import load_config


@click.command("chat")
def command() -> None:
    """Interactive Claude chat session (transcript saved in vault)."""
    cfg = load_config()
    chat.run_repl(cfg.vault, cfg.chat_model)
