"""`om chat` runtime — Claude Agent SDK REPL with JSONL transcript.

Authenticates via the bundled `claude` CLI binary that the Claude Agent SDK
spawns as a subprocess. That CLI uses your Claude Code login session, so a
Max subscription (or any active `claude login`) covers usage — no API key
required. Each invocation starts a fresh `ClaudeSDKClient`, which manages
conversation history inside the session. Every turn is appended to
`<vault>/.om/conversations/chat-<UTC-timestamp>.jsonl` so the auto-commit
hook on CLI close picks it up.
"""

from __future__ import annotations

import asyncio
import json
import pathlib
from datetime import UTC, datetime
from typing import Any

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    ClaudeSDKError,
    ResultMessage,
    StreamEvent,
    TextBlock,
)
from rich.console import Console

from om import ui

# Read-only tools we expose to the chat session. The user's vault is
# mounted as cwd, so `Read`/`Glob`/`Grep` operate against their notes.
# `Bash`, `Write`, `Edit`, `WebSearch`-style mutators are intentionally
# absent — chat must never modify the vault.
ALLOWED_TOOLS = ("Read", "Glob", "Grep", "WebFetch", "WebSearch")

# Vault-aware system prompt. The SDK's default is the full Claude Code
# agent persona — that's why Claude was trying to call MCP servers and
# emitting agent-style responses. Replace it with a focused assistant
# that knows it has read-only access to the user's notes vault.
SYSTEM_PROMPT = (
    "You are a helpful assistant embedded in the user's notes vault. "
    "The current working directory is the vault root. You have read-only "
    "access via the Read, Glob, and Grep tools — use them when the user "
    "asks about their notes, files, or anything in the vault. You also "
    "have WebFetch and WebSearch for looking things up online. You do "
    "NOT have Bash, Write, or Edit — you cannot run shell commands or "
    "modify the vault. Respond conversationally and concisely. If the "
    "vault contains a CLAUDE.md at the root, treat it as authoritative "
    "context about the user and their notes."
)


def conversation_path(vault: pathlib.Path, started_at: datetime) -> pathlib.Path:
    stamp = started_at.astimezone(UTC).strftime("%Y%m%d-%H%M%S")
    return vault / ".om" / "conversations" / f"chat-{stamp}.jsonl"


def append_jsonl(path: pathlib.Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")


def _now_utc() -> datetime:
    return datetime.now(UTC)


def _make_client(vault: pathlib.Path) -> ClaudeSDKClient:
    """Vault-aware chat session.

    The SDK still spawns the bundled `claude` CLI (so Claude Code
    subscription auth works), with `cwd` pinned to the vault so any
    relative paths the model uses resolve inside the user's notes.

    Tool surface: Read/Glob/Grep + WebFetch/WebSearch — all read-only.
    Bash/Write/Edit/etc. are excluded so the model cannot mutate the
    vault or shell out. `setting_sources=["project"]` lets a vault-local
    `<vault>/.claude/` config and a `CLAUDE.md` at the vault root flow
    through; `user` and `local` are excluded so we don't inherit the
    user's global Claude Code agent prompt and MCP servers.

    `include_partial_messages=True` streams text deltas via `StreamEvent`
    so tokens render as they arrive rather than landing in one buffered
    chunk at end-of-turn.
    """
    options = ClaudeAgentOptions(
        system_prompt=SYSTEM_PROMPT,
        tools=list(ALLOWED_TOOLS),
        allowed_tools=list(ALLOWED_TOOLS),
        mcp_servers={},
        strict_mcp_config=True,
        skills=[],
        setting_sources=["project"],
        cwd=str(vault),
        include_partial_messages=True,
    )
    return ClaudeSDKClient(options=options)


def run_repl(vault: pathlib.Path) -> None:
    asyncio.run(_repl_async(vault))


async def _repl_async(vault: pathlib.Path) -> None:
    started_at = _now_utc()
    transcript = conversation_path(vault, started_at)
    stdout = Console(highlight=False, soft_wrap=True)

    ui.info("chat session — Claude Code subscription")
    ui.info(f"vault: {vault}")
    ui.info(f"transcript: {transcript}")
    ui.info("Ctrl-D or Ctrl-C to exit")

    async with _make_client(vault) as client:
        while True:
            try:
                user_text = await asyncio.to_thread(_read_line, "> ")
            except EOFError:
                stdout.print()
                return
            user_text = user_text.strip()
            if not user_text:
                continue

            append_jsonl(
                transcript,
                {"ts": _now_utc().isoformat(), "role": "user", "content": user_text},
            )

            try:
                await _run_turn(client, user_text, stdout, transcript)
            except ClaudeSDKError as exc:
                ui.error(f"claude error: {exc}")
                continue
            except KeyboardInterrupt:
                stdout.print()
                ui.warn("interrupted")
                continue


def _read_line(prompt: str) -> str:
    """Blocking stdin read — runs in a thread so it doesn't block the event
    loop. Click's `prompt` doesn't compose with asyncio cleanly."""
    return input(prompt)


async def _run_turn(
    client: ClaudeSDKClient,
    user_text: str,
    stdout: Console,
    transcript: pathlib.Path,
) -> None:
    """Drive one turn: stream text deltas to stdout, then collect the
    final AssistantMessage (authoritative content) and ResultMessage
    (cost + duration) for the JSONL record.

    Text rendering uses the `StreamEvent` partial-message stream so the
    user sees tokens as they arrive. The `AssistantMessage` arrives once
    at end-of-turn with the full assembled content — we use that as the
    source of truth for what to log, since deltas can in theory be
    revised. To avoid printing the whole reply twice, we skip the
    AssistantMessage's TextBlocks at render time but still read them for
    the transcript.
    """
    await client.query(user_text)
    final_text = ""
    model: str | None = None
    usage: dict[str, Any] | None = None
    stop_reason: str | None = None
    cost_usd: float | None = None
    saw_stream_text = False

    async for msg in client.receive_response():
        if isinstance(msg, StreamEvent):
            delta_text = _extract_text_delta(msg.event)
            if delta_text:
                stdout.out(delta_text, end="")
                saw_stream_text = True
        elif isinstance(msg, AssistantMessage):
            text_blocks = [b.text for b in msg.content if isinstance(b, TextBlock)]
            assembled = "".join(text_blocks)
            if not saw_stream_text and assembled:
                # Older CLIs / non-streaming paths: nothing came through
                # StreamEvent, so render the assembled message now.
                stdout.out(assembled, end="")
            final_text = assembled
            model = msg.model
            if msg.usage is not None:
                usage = msg.usage
            if msg.stop_reason is not None:
                stop_reason = msg.stop_reason
        elif isinstance(msg, ResultMessage):
            cost_usd = msg.total_cost_usd
            if msg.usage is not None and usage is None:
                usage = msg.usage
            if msg.stop_reason is not None and stop_reason is None:
                stop_reason = msg.stop_reason
    stdout.print()

    record: dict[str, Any] = {
        "ts": _now_utc().isoformat(),
        "role": "assistant",
        "content": final_text,
        "model": model,
        "usage": usage,
        "stop_reason": stop_reason,
        "cost_usd": cost_usd,
    }
    append_jsonl(transcript, record)


def _extract_text_delta(event: dict[str, Any]) -> str:
    """Pull a text fragment out of a raw Anthropic stream event, or
    return ''. Handles `content_block_delta` / `text_delta` shapes."""
    if event.get("type") != "content_block_delta":
        return ""
    delta = event.get("delta")
    if not isinstance(delta, dict):
        return ""
    if delta.get("type") != "text_delta":
        return ""
    text = delta.get("text")
    return text if isinstance(text, str) else ""
