"""Rich rendering for one `treebeard chat` turn.

`chat.py` owns the SDK + transcript I/O; this module owns the visuals.
A `TurnRenderer` is constructed per turn, fed every message yielded by
`ClaudeSDKClient.receive_response()`, then `finalize()` is called to
close the active `Live`, print the per-turn footer, and hand back the
metadata `chat.py` needs to log to JSONL.

Two display modes:

- **Rich (TTY).** Streamed Markdown body in a `Live`; tool calls render
  as small bordered cards that flip from a `running…` spinner to
  `✓ summary` (or `✗ error`); a dim per-turn footer line lands after
  the response with `model · tokens · cost · duration`.
- **Plain (non-TTY).** Same data, no Live, no panels — text deltas
  print straight to stdout, tool calls show as `[tool: Read foo.md]` →
  `[tool: ✓ Read foo.md]` lines. Pipe-safe and stable for tests run
  through `CliRunner`.

The state machine is intentionally simple: each `AssistantMessage` is a
phase. A phase ending in `ToolUseBlock`s closes the response Live (its
final frame becomes static terminal output), prints one running card per
call, and waits for the next `UserMessage` (where the matching
`ToolResultBlock`s arrive) to flip each card to its final state.
"""

from __future__ import annotations

import urllib.parse
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from claude_agent_sdk import (
    AssistantMessage,
    ResultMessage,
    ServerToolResultBlock,
    ServerToolUseBlock,
    StreamEvent,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
)
from rich.console import Console
from rich.live import Live
from rich.markdown import Markdown
from rich.panel import Panel
from rich.spinner import Spinner
from rich.text import Text

# Substring that identifies the archive-guard hook's denial reason. When
# we see it inside a `ToolResultBlock`, we surface it with a friendlier
# `archive denied:` prefix instead of the model-facing wording.
_ARCHIVE_DENIAL_MARKER = "off-limits to chat"

# Width cap for tool-result summaries inside a card body. Cards live on
# one line; anything longer would wrap awkwardly inside the panel.
_RESULT_SUMMARY_WIDTH = 80


class SpinnerState(StrEnum):
    """What the outer "thinking…" spinner should say right now."""

    AWAIT = "await"
    TOOL = "tool"
    COMPOSING = "composing"


@dataclass
class TurnSummary:
    """Per-turn metadata `chat.py` needs to land in the JSONL transcript.

    Mirrors the fields the old `_run_turn` collected inline. Returning a
    dataclass instead of a tuple makes the call site readable and lets
    us add fields (e.g. `cache_read_tokens`) later without breaking
    callers.
    """

    text: str = ""
    model: str | None = None
    usage: dict[str, Any] | None = None
    stop_reason: str | None = None
    cost_usd: float | None = None
    duration_ms: int | None = None


@dataclass
class _PendingTool:
    """Live + block + finished flag for one in-flight tool card."""

    block: ToolUseBlock | ServerToolUseBlock
    live: Live | None  # None in plain mode
    resolved: bool = False


def _extract_text_delta(event: dict[str, Any]) -> str:
    """Pull a text fragment out of a raw Anthropic stream event, or ''.

    Moved from `chat.py`. Handles `content_block_delta` / `text_delta`
    shapes; everything else (tool-use deltas, message_start, etc.) is
    ignored at this layer — tool-use rendering is driven by the
    assembled `AssistantMessage`, not by raw JSON-fragment partials.
    """
    if event.get("type") != "content_block_delta":
        return ""
    delta = event.get("delta")
    if not isinstance(delta, dict):
        return ""
    if delta.get("type") != "text_delta":
        return ""
    text = delta.get("text")
    return text if isinstance(text, str) else ""


def _stream_event_tool_name(event: dict[str, Any]) -> str | None:
    """Return the tool name when a `content_block_start` announces a
    tool_use block, else None.

    Used to swap the outer spinner's text from `thinking…` to a
    tool-specific phrase as soon as the model declares the tool, before
    its full input has streamed in.
    """
    if event.get("type") != "content_block_start":
        return None
    block = event.get("content_block")
    if not isinstance(block, dict):
        return None
    if block.get("type") not in ("tool_use", "server_tool_use"):
        return None
    name = block.get("name")
    return name if isinstance(name, str) else None


def format_token_count(n: int) -> str:
    """`42` → `"42"`, `999` → `"999"`, `1234` → `"1.2k"`, `1000` → `"1k"`."""
    if n < 1000:
        return str(n)
    value = n / 1000
    if value == int(value):
        return f"{int(value)}k"
    return f"{value:.1f}k"


def format_duration(ms: int) -> str:
    """`850` → `"850ms"`, `4234` → `"4.2s"`."""
    if ms < 1000:
        return f"{ms}ms"
    return f"{ms / 1000:.1f}s"


def spinner_text_for(state: SpinnerState, tool_name: str | None = None) -> str:
    """Map (state, tool_name) → human label for the outer spinner.

    Names align with the `ALLOWED_TOOLS` set in `chat.py` plus the
    server tools (`web_search`, `web_fetch`). Unknown tools fall back to
    `working with <name>…` so we never lie about what's happening.
    """
    if state is SpinnerState.AWAIT:
        return "thinking…"
    if state is SpinnerState.COMPOSING:
        return "composing reply…"
    # SpinnerState.TOOL
    match tool_name:
        case "Read":
            return "reading notes…"
        case "Glob":
            return "searching paths…"
        case "Grep":
            return "searching content…"
        case "WebFetch" | "web_fetch":
            return "fetching…"
        case "WebSearch" | "web_search":
            return "searching the web…"
        case None | "":
            return "working…"
        case _:
            return f"working with {tool_name}…"


def tool_label(block: ToolUseBlock | ServerToolUseBlock) -> Text:
    """Title for a tool card: tool name + a compact rendering of inputs.

    Defensive: any unfamiliar tool falls through to the bare name with
    no input echo. We never want to dump arbitrary JSON into the panel
    border.
    """
    name = block.name
    args = block.input or {}
    text = Text(no_wrap=True)
    text.append(name, style="bold")

    detail: str | None = None
    if name == "Read":
        path = args.get("file_path")
        if isinstance(path, str) and path:
            detail = _basename(path)
    elif name == "Glob":
        pattern = args.get("pattern")
        path = args.get("path")
        if isinstance(pattern, str) and pattern:
            detail = pattern
            if isinstance(path, str) and path and path != ".":
                detail = f"{pattern} in {path}"
    elif name == "Grep":
        pattern = args.get("pattern")
        path = args.get("path")
        if isinstance(pattern, str) and pattern:
            detail = f'"{pattern}"'
            if isinstance(path, str) and path and path != ".":
                detail = f'"{pattern}" in {path}'
    elif name in ("WebFetch", "web_fetch"):
        url = args.get("url")
        if isinstance(url, str) and url:
            detail = _hostname(url) or url[:60]
    elif name in ("WebSearch", "web_search"):
        query = args.get("query")
        if isinstance(query, str) and query:
            detail = f'"{query}"'

    if detail:
        text.append(" ")
        text.append(detail, style="cyan")
    return text


def _basename(path: str) -> str:
    """Last path segment, with no slash special-casing — display only."""
    return path.rsplit("/", 1)[-1] or path


def _hostname(url: str) -> str | None:
    """Best-effort hostname for a `WebFetch` card title."""
    try:
        parsed = urllib.parse.urlparse(url)
    except ValueError:
        return None
    return parsed.hostname or None


def summarize_tool_result(block: ToolResultBlock | ServerToolResultBlock) -> str:
    """One-line summary of a tool result, or the archive-guard message.

    String content → first non-empty line, truncated. List content →
    entry count. Server tool result content (which is a dict) → either
    a known shape we've reduced, or `done`. Empty/None → `done`.
    """
    content = block.content
    if isinstance(content, str):
        return _archive_or_truncate(content)
    if isinstance(content, list):
        # Tool results from Claude Code come as a list of dicts —
        # typically one `{"type": "text", "text": "..."}` entry. Pull
        # the first text line if available; otherwise fall back to a
        # count.
        for entry in content:
            if isinstance(entry, dict) and entry.get("type") == "text":
                text = entry.get("text")
                if isinstance(text, str) and text.strip():
                    return _archive_or_truncate(text)
        n = len(content)
        return f"{n} item" if n == 1 else f"{n} items"
    if isinstance(content, dict):
        # Server-side tools (web_search, web_fetch) — opaque shape; we
        # don't want to leak raw JSON into the card.
        return "done"
    return "done"


def _archive_or_truncate(text: str) -> str:
    """If `text` carries the archive-guard marker, surface it with a
    friendlier prefix. Otherwise, return the first non-empty line
    truncated to `_RESULT_SUMMARY_WIDTH`."""
    stripped = text.strip()
    if _ARCHIVE_DENIAL_MARKER in stripped:
        first = stripped.splitlines()[0].strip()
        prefix = "archive denied: "
        budget = _RESULT_SUMMARY_WIDTH - len(prefix)
        if len(first) > budget:
            first = first[: budget - 1] + "…"
        return prefix + first
    for line in stripped.splitlines():
        line = line.strip()
        if line:
            if len(line) > _RESULT_SUMMARY_WIDTH:
                return line[: _RESULT_SUMMARY_WIDTH - 1] + "…"
            return line
    return "done"


def format_footer(
    model: str | None,
    usage: dict[str, Any] | None,
    cost_usd: float | None,
    duration_ms: int | None,
) -> Text:
    """Build the dim per-turn footer line.

    `claude-sonnet-4-6 · 1.2k in / 340 out · $0.0123 · 4.2s`

    Cost handling matches `_render_summary`'s convention: `None` or
    `0.0` → `subscription`, anything else → `${v:.4f}`. Pieces with
    missing data are omitted (e.g. no usage → no token segment) so the
    line stays honest rather than displaying `0 in / 0 out`.
    """
    parts: list[str] = []
    if model:
        parts.append(model)

    if usage:
        in_tok = int(usage.get("input_tokens") or 0)
        out_tok = int(usage.get("output_tokens") or 0)
        if in_tok or out_tok:
            parts.append(f"{format_token_count(in_tok)} in / {format_token_count(out_tok)} out")

    if cost_usd is None or cost_usd == 0.0:
        parts.append("subscription")
    else:
        parts.append(f"${cost_usd:.4f}")

    if duration_ms is not None and duration_ms > 0:
        parts.append(format_duration(duration_ms))

    return Text(" · ".join(parts), style="dim")


# ---------------------------------------------------------------------------
# TurnRenderer
# ---------------------------------------------------------------------------


@dataclass
class _TurnState:
    """Accumulated per-turn metadata. Built up as messages arrive."""

    text_chunks: list[str] = field(default_factory=list)
    model: str | None = None
    usage: dict[str, Any] | None = None
    stop_reason: str | None = None
    cost_usd: float | None = None
    duration_ms: int | None = None


class TurnRenderer:
    """Render one chat turn to a Rich Console.

    Lifecycle:

        renderer = TurnRenderer(out)
        async for msg in client.receive_response():
            await renderer.consume(msg)
        summary = renderer.finalize()

    `finalize()` is idempotent — `chat.py` calls it inside the normal
    flow, but it's also safe to call from an `except` branch when
    `Ctrl-C` interrupts a turn, to make sure no Live frame is left
    hanging in the terminal.
    """

    def __init__(self, console: Console, plain: bool | None = None) -> None:
        self.console = console
        # `is_terminal` is the right gate: pipes, file redirects, and
        # `CliRunner` all set it to False.
        self.plain = (not console.is_terminal) if plain is None else plain
        self.state = _TurnState()

        self._text_live: Live | None = None
        self._text_buffer: list[str] = []
        # The outer "thinking…" spinner — open when we're waiting for
        # the next phase (no text live yet, no tool cards in flight).
        self._outer_live: Live | None = None
        self._outer_state = SpinnerState.AWAIT
        self._outer_tool_name: str | None = None
        self._pending_tools: dict[str, _PendingTool] = {}
        self._finalized = False

    # -- public API --------------------------------------------------------

    async def consume(self, msg: Any) -> None:
        """Route one SDK message to the right rendering path.

        Wrapped in a try/except that closes any open Live on an
        unexpected exception — without this, a stray bug below would
        leave the terminal in spinner-frame state.
        """
        try:
            if isinstance(msg, StreamEvent):
                self._on_stream_event(msg.event)
            elif isinstance(msg, AssistantMessage):
                self._on_assistant(msg)
            elif isinstance(msg, UserMessage):
                self._on_user(msg)
            elif isinstance(msg, ResultMessage):
                self._on_result(msg)
        except Exception:
            self._close_all_lives()
            raise

    def finalize(self) -> TurnSummary:
        """Close any open Live, print the footer, return per-turn data."""
        if self._finalized:
            return self._summary()
        self._finalized = True
        self._close_all_lives()
        footer = format_footer(
            self.state.model,
            self.state.usage,
            self.state.cost_usd,
            self.state.duration_ms,
        )
        # Footer is dim regardless of TTY; print straight to the same
        # console as the response so it stays adjacent.
        if str(footer):
            self.console.print(footer)
        return self._summary()

    # -- routing -----------------------------------------------------------

    def _on_stream_event(self, event: dict[str, Any]) -> None:
        # Text deltas: into the active text Live (or plain stdout).
        delta = _extract_text_delta(event)
        if delta:
            self._append_text(delta)
            return
        # Tool-use start: swap outer spinner if it's still showing.
        tool_name = _stream_event_tool_name(event)
        if tool_name and self._outer_live is not None:
            self._set_outer_spinner(SpinnerState.TOOL, tool_name)

    def _on_assistant(self, msg: AssistantMessage) -> None:
        # Capture metadata regardless of content shape.
        if msg.model:
            self.state.model = msg.model
        if msg.usage is not None:
            self.state.usage = msg.usage
        if msg.stop_reason is not None:
            self.state.stop_reason = msg.stop_reason

        # Walk content. A phase can mix text and tool calls; render in
        # declaration order.
        text_parts: list[str] = []
        tool_blocks: list[ToolUseBlock | ServerToolUseBlock] = []
        for block in msg.content:
            if isinstance(block, TextBlock):
                text_parts.append(block.text)
            elif isinstance(block, ToolUseBlock | ServerToolUseBlock):
                tool_blocks.append(block)
            # ThinkingBlock / ToolResultBlock not expected in
            # AssistantMessage with our config — silently ignore.

        # If the deltas already populated the buffer for this phase, we
        # don't re-print the assembled text — it's already rendered.
        # Otherwise, this is a non-streaming reply; render the whole
        # text in one shot.
        assembled = "".join(text_parts)
        if assembled and not self._text_buffer:
            self._append_text(assembled)
        # Either way, persist the assembled text on the turn.
        if assembled:
            self.state.text_chunks.append(assembled)

        if tool_blocks:
            # Phase ends with tool calls: close the text Live so the
            # frame becomes static terminal output, then print one card
            # per call.
            self._close_text_live()
            self._close_outer_live()
            for block in tool_blocks:
                self._open_tool_card(block)
            # While tools are running, the outer spinner shows what
            # they're doing.
            primary = tool_blocks[0].name
            self._set_outer_spinner(SpinnerState.TOOL, primary)

    def _on_user(self, msg: UserMessage) -> None:
        # We only care about tool-result content. String UserMessages
        # are echoes of the user's own prompt — they pre-existed in the
        # transcript and aren't ours to render.
        if not isinstance(msg.content, list):
            return
        any_resolved = False
        for block in msg.content:
            if isinstance(block, ToolResultBlock | ServerToolResultBlock):
                self._resolve_tool_card(block)
                any_resolved = True
        if any_resolved:
            self._close_outer_live()
            # After tools resolve, we expect the model to keep talking;
            # show "composing reply…" until the next AssistantMessage.
            self._set_outer_spinner(SpinnerState.COMPOSING, None)

    def _on_result(self, msg: ResultMessage) -> None:
        self.state.cost_usd = msg.total_cost_usd
        self.state.duration_ms = msg.duration_ms
        if msg.usage is not None and self.state.usage is None:
            self.state.usage = msg.usage
        if msg.stop_reason is not None and self.state.stop_reason is None:
            self.state.stop_reason = msg.stop_reason

    # -- text rendering ----------------------------------------------------

    def _append_text(self, delta: str) -> None:
        if self.plain:
            # Plain stdout streaming: write directly, no Live.
            self.console.out(delta, end="")
            self._text_buffer.append(delta)
            return
        if self._text_live is None:
            self._open_text_live()
        self._text_buffer.append(delta)
        if self._text_live is not None:
            self._text_live.update(Markdown("".join(self._text_buffer)))

    def _open_text_live(self) -> None:
        # Close the outer spinner; the text Live takes over the role of
        # "something is happening on screen."
        self._close_outer_live()
        self._text_buffer = []
        self._text_live = Live(
            Markdown(""),
            console=self.console,
            refresh_per_second=12,
            transient=False,
            vertical_overflow="visible",
        )
        self._text_live.__enter__()

    def _close_text_live(self) -> None:
        if self._text_live is None:
            if self.plain and self._text_buffer:
                # Ensure plain-mode text ends on a newline before the
                # next phase prints a tool card or footer.
                self.console.out("\n", end="")
                self._text_buffer = []
            return
        try:
            self._text_live.__exit__(None, None, None)
        finally:
            self._text_live = None
            self._text_buffer = []

    # -- tool card rendering ----------------------------------------------

    def _open_tool_card(self, block: ToolUseBlock | ServerToolUseBlock) -> None:
        if self.plain:
            label = tool_label(block).plain
            self.console.print(f"[tool: {label}]", markup=False, highlight=False)
            self._pending_tools[block.id] = _PendingTool(block=block, live=None)
            return
        title = tool_label(block)
        body = Spinner("dots", text=Text("running…", style="dim"))
        panel = Panel(
            body,
            title=title,
            title_align="left",
            border_style="dim",
            padding=(0, 1),
            expand=False,
        )
        live = Live(
            panel,
            console=self.console,
            refresh_per_second=12,
            transient=False,
            vertical_overflow="visible",
        )
        live.__enter__()
        self._pending_tools[block.id] = _PendingTool(block=block, live=live)

    def _resolve_tool_card(self, result: ToolResultBlock | ServerToolResultBlock) -> None:
        pending = self._pending_tools.get(result.tool_use_id)
        if pending is None or pending.resolved:
            return
        pending.resolved = True
        summary = summarize_tool_result(result)
        is_error = bool(getattr(result, "is_error", False))

        if self.plain:
            label = tool_label(pending.block).plain
            mark = "✗" if is_error else "✓"
            self.console.print(f"[tool: {mark} {label}] {summary}", markup=False, highlight=False)
            return

        glyph = Text("✗ ", style="red") if is_error else Text("✓ ", style="green")
        body = Text.assemble(glyph, Text(summary, style="dim"))
        panel = Panel(
            body,
            title=tool_label(pending.block),
            title_align="left",
            border_style="dim",
            padding=(0, 1),
            expand=False,
        )
        if pending.live is not None:
            pending.live.update(panel)
            pending.live.__exit__(None, None, None)
            pending.live = None

    # -- outer spinner -----------------------------------------------------

    def _set_outer_spinner(self, state: SpinnerState, tool_name: str | None) -> None:
        self._outer_state = state
        self._outer_tool_name = tool_name
        if self.plain:
            return
        text = spinner_text_for(state, tool_name)
        renderable = Spinner("dots", text=Text(text, style="dim"))
        if self._outer_live is None:
            self._outer_live = Live(
                renderable,
                console=self.console,
                refresh_per_second=12,
                transient=True,
                vertical_overflow="visible",
            )
            self._outer_live.__enter__()
        else:
            self._outer_live.update(renderable)

    def _close_outer_live(self) -> None:
        if self._outer_live is None:
            return
        try:
            self._outer_live.__exit__(None, None, None)
        finally:
            self._outer_live = None

    def _close_all_lives(self) -> None:
        # Resolve any unresolved tool cards as a generic "interrupted"
        # so the user doesn't see a frozen spinner card after Ctrl-C.
        for pending in self._pending_tools.values():
            if pending.resolved or pending.live is None:
                continue
            pending.resolved = True
            try:
                if not self.plain:
                    body = Text.assemble(
                        Text("⚠ ", style="yellow"),
                        Text("interrupted", style="dim"),
                    )
                    pending.live.update(
                        Panel(
                            body,
                            title=tool_label(pending.block),
                            title_align="left",
                            border_style="dim",
                            padding=(0, 1),
                            expand=False,
                        )
                    )
                pending.live.__exit__(None, None, None)
            except Exception:
                # Best-effort cleanup — never raise from cleanup.
                pass
            pending.live = None
        self._close_text_live()
        self._close_outer_live()

    # -- summary -----------------------------------------------------------

    def _summary(self) -> TurnSummary:
        text = "".join(self.state.text_chunks)
        return TurnSummary(
            text=text,
            model=self.state.model,
            usage=self.state.usage,
            stop_reason=self.state.stop_reason,
            cost_usd=self.state.cost_usd,
            duration_ms=self.state.duration_ms,
        )
