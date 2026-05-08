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
from importlib import resources
from typing import Any

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    ClaudeSDKError,
    HookMatcher,
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

# Vault-relative directory holding soft-deleted notes (see `om archive`).
# Chat must never read these — they were intentionally taken out of the
# active set, and surfacing them would re-introduce stale context the
# user already retired.
ARCHIVE_REL_DIR = pathlib.PurePosixPath(".om/archive")

# Vault-aware system prompt. The SDK's default is the full Claude Code
# agent persona — that's why Claude was trying to call MCP servers and
# emitting agent-style responses. Replace it with a focused assistant
# that knows it has read-only access to the user's notes vault.
#
# Loaded from `om/prompts/system_prompt.txt` so edits don't require
# touching Python. Read once at import; the file is a package-shipped
# constant.
SYSTEM_PROMPT = (
    resources.files("om").joinpath("prompts/system_prompt.txt").read_text(encoding="utf-8").strip()
)

# Glyph for the user-input prompt — matches `om init`'s aesthetic.
PROMPT_GLYPH = "▸"

# REPL-internal commands that exit the loop without being sent to Claude.
# Compared after stripping. Slash forms are the documented surface (shown
# in the header); bare aliases catch the common typo of forgetting the
# slash and aren't advertised.
EXIT_COMMANDS = frozenset({"/exit", "/quit", "exit", "quit"})

# Slash commands surfaced in the header. Each entry is `(command, blurb)`;
# the rendered list is alphabetized by command. Keep this in sync with
# `EXIT_COMMANDS` (slash forms) when a new REPL command is added.
SLASH_COMMANDS: tuple[tuple[str, str], ...] = (
    ("/exit", "end the session"),
    ("/quit", "end the session"),
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


def _path_targets_archive(value: str, vault: pathlib.Path) -> bool:
    """Resolve `value` against `vault` and return True if it lands inside
    `.om/archive/`. Accepts both vault-relative and absolute paths; an
    unparseable value is treated as not-archive (the model will get a
    normal tool error from Claude Code itself)."""
    if not value:
        return False
    try:
        candidate = pathlib.Path(value)
        if not candidate.is_absolute():
            candidate = vault / candidate
        resolved = candidate.resolve(strict=False)
    except (OSError, ValueError):
        return False
    archive_root = (vault / ARCHIVE_REL_DIR).resolve(strict=False)
    return resolved == archive_root or archive_root in resolved.parents


def _archive_guard_hook(vault: pathlib.Path) -> Any:
    """PreToolUse hook: deny Read/Glob/Grep calls that target the archive.

    Returned as an `async` callable matching `HookCallback`. We inspect
    the path-bearing fields each tool uses (`file_path` for Read,
    `path`/`pattern` for Glob and Grep) and short-circuit with a deny
    decision; the model sees the reason and can adjust.
    """

    async def _hook(input_data: Any, _tool_use_id: str | None, _ctx: Any) -> dict[str, Any]:
        tool_input = input_data.get("tool_input") or {}
        for key in ("file_path", "path", "pattern"):
            value = tool_input.get(key)
            if isinstance(value, str) and _path_targets_archive(value, vault):
                return {
                    "hookSpecificOutput": {
                        "hookEventName": "PreToolUse",
                        "permissionDecision": "deny",
                        "permissionDecisionReason": (
                            f"`{ARCHIVE_REL_DIR}` is off-limits to chat — these "
                            "notes were archived intentionally and must not be "
                            "read or searched."
                        ),
                    }
                }
        return {}

    return _hook


def _make_client(vault: pathlib.Path, model: str) -> ClaudeSDKClient:
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
        model=model,
        hooks={
            "PreToolUse": [
                HookMatcher(
                    matcher="Read|Glob|Grep",
                    hooks=[_archive_guard_hook(vault)],
                ),
            ],
        },
        # Latency: skip silent reasoning before the first token. Casual
        # chat doesn't benefit much from extended thinking, and disabling
        # it cuts several seconds off TTFT on short replies.
        thinking=ThinkingConfigDisabled(type="disabled"),
        # Latency: low effort means fewer/consolidated tool calls, less
        # preamble, terser confirmations. Best fit for chat.
        effort="low",
    )
    return ClaudeSDKClient(options=options)


def run_repl(vault: pathlib.Path, model: str) -> None:
    asyncio.run(_repl_async(vault, model))


async def _repl_async(vault: pathlib.Path, model: str) -> None:
    started_at = _now_utc()
    transcript = conversation_path(vault, started_at)
    out = Console(highlight=False)

    _render_header(vault, transcript, model)

    try:
        async with _make_client(vault, model) as client:
            while True:
                try:
                    user_text = await asyncio.to_thread(_read_line)
                except EOFError:
                    out.print()
                    break
                user_text = user_text.strip()
                if not user_text:
                    continue
                if user_text in EXIT_COMMANDS:
                    break

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
    finally:
        _render_summary(transcript)


def _render_header(vault: pathlib.Path, transcript: pathlib.Path, model: str) -> None:
    parts: list[tuple[str, str]] = [
        ("vault      ", "dim"),
        (f"{vault}\n", "white"),
        ("transcript ", "dim"),
        (f"{transcript}\n", "white"),
        ("model      ", "dim"),
        (f"{model}\n", "white"),
        ("commands   ", "dim"),
    ]
    sorted_commands = sorted(SLASH_COMMANDS, key=lambda pair: pair[0])
    for index, (cmd, blurb) in enumerate(sorted_commands):
        if index > 0:
            parts.append(("\n           ", "dim"))
        parts.append((f"{cmd}  ", "white"))
        parts.append((blurb, "dim"))
    body = Text.assemble(*parts)
    status_console.print(
        Panel(
            body,
            title="[bold]om chat[/bold]",
            subtitle="[dim]Claude Code subscription · read-only vault access[/dim]",
            border_style="cyan",
            expand=False,
        )
    )


def _render_summary(transcript: pathlib.Path) -> None:
    """Print a compact session summary by reading back the transcript.

    The JSONL is the source of truth — each assistant record carries the
    `usage` dict and `cost_usd` for that turn, so we don't need to track
    running totals in memory. Silent no-op if the transcript is missing
    or has no assistant turns (e.g. the user opened chat and exited
    immediately, or every turn errored before producing a reply).
    """
    if not transcript.exists():
        return

    turns = 0
    total_in = 0
    total_out = 0
    total_cost = 0.0
    saw_any_cost = False
    saw_null_cost = False

    for line in transcript.read_text(encoding="utf-8").splitlines():
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if record.get("role") != "assistant":
            continue
        turns += 1
        usage = record.get("usage") or {}
        if isinstance(usage, dict):
            total_in += int(usage.get("input_tokens") or 0)
            total_out += int(usage.get("output_tokens") or 0)
        cost = record.get("cost_usd")
        if cost is None:
            saw_null_cost = True
        else:
            total_cost += float(cost)
            saw_any_cost = True

    if turns == 0:
        return

    if not saw_any_cost:
        cost_line: tuple[str, str] = ("subscription", "white")
    elif saw_null_cost:
        cost_line = (f"${total_cost:.4f} (partial)", "white")
    else:
        cost_line = (f"${total_cost:.4f}", "white")

    body = Text.assemble(
        ("turns      ", "dim"),
        (f"{turns}\n", "white"),
        ("tokens     ", "dim"),
        (f"{total_in} in / {total_out} out\n", "white"),
        ("cost       ", "dim"),
        cost_line,
    )
    status_console.print(
        Panel(
            body,
            title="[bold]session summary[/bold]",
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
