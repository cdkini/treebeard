"""JSONL transcript I/O for the chat session.

Each invocation appends to `<vault>/.treebeard/conversations/chat-<UTC>.jsonl`
so the auto-commit hook on CLI close picks it up. Pure I/O — no SDK
dependencies — so the indexer / archiver can read transcripts back
without dragging in `claude_agent_sdk`.
"""

from __future__ import annotations

import json
import pathlib
from datetime import UTC, datetime
from typing import Any

from treebeard import vault_layout


def conversation_path(vault: pathlib.Path, started_at: datetime) -> pathlib.Path:
    stamp = started_at.astimezone(UTC).strftime("%Y%m%d-%H%M%S")
    return vault_layout.conversations_dir(vault) / f"chat-{stamp}.jsonl"


def append_jsonl(path: pathlib.Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")
