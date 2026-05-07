"""`om chat` runtime — Claude Agent SDK REPL with JSONL transcript.

Authenticates via the bundled `claude` CLI binary that the Claude Agent SDK
spawns as a subprocess. That CLI uses your Claude Code login session, so a
Max subscription (or any active `claude login`) covers usage — no API key
required. Each invocation starts a fresh `ClaudeSDKClient`, which manages
conversation history inside the session. Every turn is appended to
`<vault>/.om/conversations/chat-<UTC-timestamp>.jsonl` so the auto-commit
hook on CLI close picks it up.

Rendering uses Rich: a header `Panel` at startup, then per-turn live
Markdown rendering inside a `rich.live.Live` context so code blocks,
lists, and headings flow as the model emits tokens. Falls back to plain
streaming when stdout isn't a TTY (e.g. piped output, tests).
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
    ThinkingConfigDisabled,
)
from rich.console import Console
from rich.live import Live
from rich.markdown import Markdown
from rich.panel import Panel
from rich.spinner import Spinner
from rich.text import Text

from om import ui
from om.ui import status_console

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

# Glyph for the user-input prompt — matches `om init`'s aesthetic.
PROMPT_GLYPH = "▸"


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
        # Latency: skip silent reasoning before the first token. Casual
        # chat doesn't benefit much from extended thinking, and disabling
        # it cuts several seconds off TTFT on short replies.
        thinking=ThinkingConfigDisabled(type="disabled"),
        # Latency: low effort means fewer/consolidated tool calls, less
        # preamble, terser confirmations. Best fit for chat.
        effort="low",
    )
    return ClaudeSDKClient(options=options)


def run_repl(vault: pathlib.Path) -> None:
    asyncio.run(_repl_async(vault))


async def _repl_async(vault: pathlib.Path) -> None:
    started_at = _now_utc()
    transcript = conversation_path(vault, started_at)
    out = Console(highlight=False)

    _render_header(vault, transcript)

    async with _make_client(vault) as client:
        while True:
            try:
                user_text = await asyncio.to_thread(_read_line)
            except EOFError:
                out.print()
                status_console.print("[dim]session ended[/dim]")
                return
            user_text = user_text.strip()
            if not user_text:
                continue

            append_jsonl(
                transcript,
                {"ts": _now_utc().isoformat(), "role": "user", "content": user_text},
            )

            try:
                await _run_turn(client, user_text, out, transcript)
            except ClaudeSDKError as exc:
                ui.error(f"claude error: {exc}")
                continue
            except KeyboardInterrupt:
                out.print()
                ui.warn("interrupted")
                continue


def _render_header(vault: pathlib.Path, transcript: pathlib.Path) -> None:
    body = Text.assemble(
        ("vault     ", "dim"),
        (f"{vault}\n", "white"),
        ("transcript ", "dim"),
        (f"{transcript}\n", "white"),
        ("exit      ", "dim"),
        ("Ctrl-D or Ctrl-C", "white"),
    )
    status_console.print(
        Panel(
            body,
            title="[bold]om chat[/bold]",
            subtitle="[dim]Claude Code subscription · read-only vault access[/dim]",
            border_style="cyan",
            expand=False,
        )
    )


def _read_line() -> str:
    """Blocking stdin read — runs in a thread so it doesn't block the
    event loop. Click's `prompt` doesn't compose with asyncio cleanly."""
    # Pre-print the styled glyph to stderr (so it survives stdout piping
    # while still being visible interactively); then a plain `input()`
    # with no further prompt does the line read.
    status_console.print(f"[bold cyan]{PROMPT_GLYPH}[/bold cyan] ", end="")
    return input()


async def _run_turn(
    client: ClaudeSDKClient,
    user_text: str,
    out: Console,
    transcript: pathlib.Path,
) -> None:
    """Drive one turn: render Markdown live as deltas arrive, then log
    the final AssistantMessage + ResultMessage to the JSONL transcript.

    Text source of truth is the assembled content from `AssistantMessage`
    (authoritative — deltas can in theory be revised). The Live panel is
    fed the running buffer of stream deltas; if no deltas arrive (older
    CLIs, non-streaming responses), we render the assembled message once
    at the end.
    """
    await client.query(user_text)
    buffer: list[str] = []
    final_text = ""
    model: str | None = None
    usage: dict[str, Any] | None = None
    stop_reason: str | None = None
    cost_usd: float | None = None

    # Start with a spinner so the user can see the system is working
    # while we wait for the first token. Swap the renderable to a
    # Markdown panel as soon as text arrives.
    with Live(
        Spinner("dots", text=Text("thinking…", style="dim")),
        console=out,
        refresh_per_second=12,
        transient=False,
        vertical_overflow="visible",
    ) as live:
        first_text_seen = False
        async for msg in client.receive_response():
            if isinstance(msg, StreamEvent):
                delta_text = _extract_text_delta(msg.event)
                if delta_text:
                    buffer.append(delta_text)
                    first_text_seen = True
                    live.update(Markdown("".join(buffer)))
            elif isinstance(msg, AssistantMessage):
                text_blocks = [b.text for b in msg.content if isinstance(b, TextBlock)]
                assembled = "".join(text_blocks)
                final_text = assembled
                if not first_text_seen and assembled:
                    # No StreamEvents arrived; render the assembled
                    # message into the Live panel in one shot.
                    live.update(Markdown(assembled))
                    first_text_seen = True
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

    # Thin separator between turns (dim rule, no body).
    status_console.rule(style="dim")

    record: dict[str, Any] = {
        "ts": _now_utc().isoformat(),
        "role": "assistant",
        "content": final_text or "".join(buffer),
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
