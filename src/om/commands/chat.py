"""`om chat` — interactive Claude REPL with JSONL transcript."""

from __future__ import annotations

import click

from om import chat
from om.config import load_config


@click.command("chat")
def command() -> None:
    """Interactive Claude chat session (transcript saved in vault)."""
    cfg = load_config()
    chat.run_repl(cfg.vault, cfg.chat_model)
