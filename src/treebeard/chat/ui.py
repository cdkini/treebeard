"""Rich rendering for one `tb chat` turn.

`session.py` owns the SDK + transcript I/O; this module owns the visuals.
A `TurnRenderer` is constructed per turn, fed every message yielded by
`ClaudeSDKClient.receive_response()`, then `finalize()` is called to
close the active `Live` and hand back the metadata `session.py` needs
to log to JSONL.

Two display modes:

- **Rich (TTY).** The assistant turn lives inside a magenta `┃` gutter
  rendered as a single `Live`. Inside the gutter: an optional grouped
  `tool calls (N)` Panel for any in-flight or resolved tool calls,
  followed by streamed Markdown prose. Errors flip the gutter red;
  Ctrl-C interrupts flip it yellow and append a `⚠ interrupted` line.
  Before any content has arrived, a transient outer "thinking…" spinner
  holds the line so the user knows something is happening.
- **Plain (non-TTY).** Same data, no Live, no gutter, no panels — text
  deltas print straight to stdout, tool calls show as `[tool: Read foo.md]`
  → `[tool: ✓ Read foo.md] <summary>` lines. Pipe-safe and stable for
  tests run through `CliRunner`.

The state machine is intentionally simple: text deltas append to a
buffer that drives the Markdown render; tool-use blocks append rows to
a grouped card that lives above the prose; tool-result blocks flip the
matching row from `running` to `✓`/`✗`. The whole gutter renderable is
rebuilt cheaply on each Live update — much simpler than mutating Rich
objects in place.
"""

from __future__ import annotations

import re
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
from rich.console import Console, ConsoleOptions, Group, RenderableType, RenderResult
from rich.live import Live
from rich.markdown import Markdown
from rich.measure import Measurement
from rich.panel import Panel
from rich.segment import Segment
from rich.spinner import Spinner
from rich.style import Style
from rich.text import Text

# Substring that identifies the archive-guard hook's denial reason. When
# we see it inside a `ToolResultBlock`, we surface it with a friendlier
# `archive denied:` prefix instead of the model-facing wording.
_ARCHIVE_DENIAL_MARKER = "off-limits to chat"

# Width cap for tool-result summaries inside a tool row.
_RESULT_SUMMARY_WIDTH = 80


class SpinnerState(StrEnum):
    """What the outer pre-content spinner should say right now."""

    AWAIT = "await"
    TOOL = "tool"
    COMPOSING = "composing"


class GutterStyle(StrEnum):
    """Bar color for the assistant gutter. Switches based on turn outcome."""

    NORMAL = "normal"
    ERROR = "error"
    INTERRUPTED = "interrupted"


@dataclass
class TurnSummary:
    """Per-turn metadata `session.py` needs to land in the JSONL transcript."""

    text: str = ""
    model: str | None = None
    usage: dict[str, Any] | None = None
    stop_reason: str | None = None
    cost_usd: float | None = None
    duration_ms: int | None = None


@dataclass
class _ToolRow:
    """One row inside the grouped `tool calls (N)` card."""

    block: ToolUseBlock | ServerToolUseBlock
    resolved: bool = False
    is_error: bool = False
    summary: str = ""


@dataclass
class _TextSegment:
    """A contiguous run of prose between tool-call phases."""

    chunks: list[str] = field(default_factory=list)

    @property
    def text(self) -> str:
        return "".join(self.chunks)


@dataclass
class _ToolGroup:
    """A grouped tool-card phase: one or more parallel tool calls."""

    rows: list[_ToolRow] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Gutter renderable
# ---------------------------------------------------------------------------


class Gutter:
    """A Rich renderable that prefixes every visual line of `child` with a
    styled vertical bar.

    Reserves 2 cells on the left for the bar character + a space. Blank
    lines get just the bar. Wraps cleanly inside a `Live` because each
    rebuild is a fresh renderable — no stateful mutation, no flicker.
    """

    def __init__(
        self,
        child: RenderableType,
        bar_style: str | Style = "magenta",
        bar_char: str = "┃",
    ) -> None:
        self.child = child
        self.bar_char = bar_char
        self.bar_style = bar_style if isinstance(bar_style, Style) else Style.parse(bar_style)

    def __rich_console__(self, console: Console, options: ConsoleOptions) -> RenderResult:
        inner_width = max(options.max_width - 2, 1)
        inner_options = options.update(width=inner_width)
        segments = console.render(self.child, inner_options)
        bar = Segment(f"{self.bar_char} ", self.bar_style)
        empty_bar = Segment(self.bar_char, self.bar_style)
        for line in Segment.split_lines(segments):
            if all(s.text.strip() == "" for s in line):
                yield empty_bar
            else:
                yield bar
                yield from line
            yield Segment.line()

    def __rich_measure__(self, console: Console, options: ConsoleOptions) -> Measurement:
        inner = Measurement.get(console, options.update(width=options.max_width - 2), self.child)
        return Measurement(inner.minimum + 2, inner.maximum + 2)


# ---------------------------------------------------------------------------
# Stream-event helpers
# ---------------------------------------------------------------------------


def _extract_text_delta(event: dict[str, Any]) -> str:
    """Pull a text fragment out of a raw Anthropic stream event, or ''."""
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
    tool_use block, else None."""
    if event.get("type") != "content_block_start":
        return None
    block = event.get("content_block")
    if not isinstance(block, dict):
        return None
    if block.get("type") not in ("tool_use", "server_tool_use"):
        return None
    name = block.get("name")
    return name if isinstance(name, str) else None


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------


def format_token_count(n: int) -> str:
    """`42` → `"42"`, `1234` → `"1.2k"`, `1000` → `"1k"`."""
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
    """Map (state, tool_name) → human label for the pre-content spinner."""
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
    """Title for a tool row: tool name + a compact rendering of inputs."""
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
    """Best-effort hostname for a `WebFetch` row title."""
    try:
        parsed = urllib.parse.urlparse(url)
    except ValueError:
        return None
    return parsed.hostname or None


def summarize_tool_result(block: ToolResultBlock | ServerToolResultBlock) -> str:
    """One-line summary of a tool result, or the archive-guard message."""
    content = block.content
    if isinstance(content, str):
        return _archive_or_truncate(content)
    if isinstance(content, list):
        for entry in content:
            if isinstance(entry, dict) and entry.get("type") == "text":
                text = entry.get("text")
                if isinstance(text, str) and text.strip():
                    return _archive_or_truncate(text)
        n = len(content)
        return f"{n} item" if n == 1 else f"{n} items"
    if isinstance(content, dict):
        return "done"
    return "done"


def _archive_or_truncate(text: str) -> str:
    """Archive-guard substring → friendly `archive denied:` prefix.
    Otherwise: a line count if the content looks like Claude Code's
    line-numbered Read output, else the first non-empty line truncated
    to `_RESULT_SUMMARY_WIDTH`.

    Claude Code's Read tool returns lines as `"     N→<content>"`,
    where `N` is the line number. Showing line 1's content as a
    summary is misleading (it's frontmatter `---` for most markdown
    notes); a line count is more useful and matches the existing
    `42 lines` style.
    """
    stripped = text.strip()
    if _ARCHIVE_DENIAL_MARKER in stripped:
        first = stripped.splitlines()[0].strip()
        prefix = "archive denied: "
        budget = _RESULT_SUMMARY_WIDTH - len(prefix)
        if len(first) > budget:
            first = first[: budget - 1] + "…"
        return prefix + first
    lines = stripped.splitlines()
    if _looks_like_line_numbered(lines):
        n = sum(1 for line in lines if _LINE_NUMBER_PREFIX.match(line))
        return f"{n} line" if n == 1 else f"{n} lines"
    for line in lines:
        line = line.strip()
        if line:
            if len(line) > _RESULT_SUMMARY_WIDTH:
                return line[: _RESULT_SUMMARY_WIDTH - 1] + "…"
            return line
    return "done"


# Claude Code's Read tool prefixes every line with `     N→` (spaces +
# line number + arrow). Match that exact shape — we don't want to false-
# positive on arbitrary text that happens to start with whitespace.
_LINE_NUMBER_PREFIX = re.compile(r"^\s*\d+→")


def _looks_like_line_numbered(lines: list[str]) -> bool:
    """True if the majority of non-empty lines match the Read-tool
    prefix. Tolerates the occasional truncation marker line (e.g. a
    `…[truncated]` sentinel after the numbered content)."""
    numbered = sum(1 for line in lines if _LINE_NUMBER_PREFIX.match(line))
    return numbered >= 2 and numbered >= len(lines) // 2


# `Refs:` is a vault-CLAUDE.md convention — a trailing block of cited
# notes formatted as a tight bullet list. Rich's Markdown renderer
# always inserts a blank line before a list, which breaks the
# "label + tight list" feel. We detect the trailer and render it as a
# compact Text instead so the bullets sit right under `Refs:`.
_REFS_TRAILER = re.compile(
    r"(?P<sep>\n+)Refs:\s*\n(?P<refs>(?:[ \t]*[-•][ \t]+.+\n?)+)\s*\Z",
    re.MULTILINE,
)
_REFS_ITEM = re.compile(r"^[ \t]*[-•][ \t]+(?P<body>.+?)\s*$")


def _split_refs_trailer(text: str) -> tuple[str, list[str] | None]:
    """Pull a trailing `Refs:` block off the end of `text`.

    Returns `(prose, refs)` where `prose` is the text with the trailer
    removed and `refs` is a list of bullet bodies (without the `-`
    marker). When no recognisable trailer is present, returns
    `(text, None)` so the caller renders normally.

    The detection is anchored to end-of-string and requires `Refs:` to
    be followed by at least one bullet item — a model that says
    "Refs:" mid-sentence won't trigger.
    """
    match = _REFS_TRAILER.search(text)
    if match is None:
        return text, None
    prose = text[: match.start()].rstrip()
    refs: list[str] = []
    for line in match.group("refs").splitlines():
        item = _REFS_ITEM.match(line)
        if item:
            refs.append(item.group("body").strip())
    if not refs:
        return text, None
    return prose, refs


def _render_refs_block(refs: list[str]) -> Text:
    """Render the Refs trailer as a tight `Refs:` label + bullet list.

    No blank line between label and bullets — that's the whole point
    of intercepting it from the Markdown renderer.
    """
    body = Text()
    body.append("Refs:", style="dim")
    for ref in refs:
        body.append("\n")
        body.append("  • ", style="dim")
        body.append(ref)
    return body


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


# Bar styles for each gutter mood.
_BAR_STYLES: dict[GutterStyle, str] = {
    GutterStyle.NORMAL: "magenta",
    GutterStyle.ERROR: "red",
    GutterStyle.INTERRUPTED: "yellow",
}


class TurnRenderer:
    """Render one chat turn to a Rich Console.

    Lifecycle:

        renderer = TurnRenderer(out)
        async for msg in client.receive_response():
            await renderer.consume(msg)
        summary = renderer.finalize()

    `finalize()` is idempotent — `session.py` calls it inside the normal
    flow, but it's also safe to call from an `except` branch when
    `Ctrl-C` interrupts a turn or an SDK error fires, to make sure no
    Live frame is left hanging in the terminal.

    Call `mark_error(msg)` or `mark_interrupted()` before finalize() to
    paint the gutter the right color and append a closing marker line.
    """

    def __init__(self, console: Console, plain: bool | None = None) -> None:
        self.console = console
        # `is_terminal` is the right gate: pipes, file redirects, and
        # `CliRunner` all set it to False.
        self.plain = (not console.is_terminal) if plain is None else plain
        self.state = _TurnState()

        # Assistant-gutter Live. Opened lazily on first content (text
        # delta or tool block) and rebuilt on every change. The
        # `_blocks` list preserves the order in which the model
        # emitted prose vs. tool calls — text-before-tool renders
        # above the tool card, text-after-tool renders below.
        self._gutter_live: Live | None = None
        self._blocks: list[_TextSegment | _ToolGroup] = []
        self._tool_index: dict[str, _ToolRow] = {}
        self._gutter_style: GutterStyle = GutterStyle.NORMAL
        self._closing_line: Text | None = None  # ✗ / ⚠ marker appended on error/interrupt

        # Pre-content spinner Live (transient). Holds the line while we
        # wait for the model to produce anything.
        self._outer_live: Live | None = None

        self._finalized = False

    # -- public API --------------------------------------------------------

    async def consume(self, msg: Any) -> None:
        """Route one SDK message to the right rendering path."""
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

    def mark_error(self, message: str) -> None:
        """Flip the gutter red and append a closing `✗ <message>` line.

        In plain mode (non-TTY), the message is emitted as a single
        line to the console so the failure is still visible in piped
        output and test transcripts.
        """
        self._gutter_style = GutterStyle.ERROR
        self._closing_line = Text.assemble(Text("✗ ", style="bold red"), Text(message, style="red"))
        if self.plain:
            self.console.print(f"[red]✗[/red] {message}")
            return
        if self._gutter_live is not None:
            self._gutter_live.update(self._build_gutter())

    def mark_interrupted(self) -> None:
        """Flip the gutter yellow and append a closing `⚠ interrupted` line.

        In plain mode, emits a single `⚠ interrupted` line so the
        non-TTY output reflects the closing state.
        """
        self._gutter_style = GutterStyle.INTERRUPTED
        self._closing_line = Text.assemble(
            Text("⚠ ", style="bold yellow"), Text("interrupted", style="yellow")
        )
        if self.plain:
            self.console.print("[yellow]⚠[/yellow] interrupted")
            return
        if self._gutter_live is not None:
            self._gutter_live.update(self._build_gutter())

    def finalize(self) -> TurnSummary:
        """Close any open Live, return per-turn data."""
        if self._finalized:
            return self._summary()
        self._finalized = True
        # Flush any buffered text that hadn't yet triggered a gutter
        # open (short replies that fit in a single paragraph). Without
        # this, a 1-paragraph reply would never render in TTY mode.
        if (
            not self.plain
            and self._gutter_live is None
            and (self._blocks or self._closing_line is not None)
        ):
            self._open_gutter_if_needed()
        self._close_all_lives()
        return self._summary()

    # -- routing -----------------------------------------------------------

    def _on_stream_event(self, event: dict[str, Any]) -> None:
        delta = _extract_text_delta(event)
        if delta:
            self._append_text(delta)
            return
        tool_name = _stream_event_tool_name(event)
        if tool_name and self._outer_live is not None:
            self._set_outer_spinner(SpinnerState.TOOL, tool_name)

    def _on_assistant(self, msg: AssistantMessage) -> None:
        if msg.model:
            self.state.model = msg.model
        if msg.usage is not None:
            self.state.usage = msg.usage
        if msg.stop_reason is not None:
            self.state.stop_reason = msg.stop_reason

        text_parts: list[str] = []
        tool_blocks: list[ToolUseBlock | ServerToolUseBlock] = []
        for block in msg.content:
            if isinstance(block, TextBlock):
                text_parts.append(block.text)
            elif isinstance(block, ToolUseBlock | ServerToolUseBlock):
                tool_blocks.append(block)

        assembled = "".join(text_parts)
        # Did streamed deltas already populate the trailing text
        # segment for this phase? If so, skip the re-append. Otherwise
        # this is a non-streaming reply — push the whole thing into the
        # trailing text segment now.
        trailing_has_deltas = (
            self._blocks
            and isinstance(self._blocks[-1], _TextSegment)
            and bool(self._blocks[-1].chunks)
        )
        if assembled and not trailing_has_deltas:
            if self.plain:
                self.console.out(assembled, end="")
            self._trailing_text_segment().chunks.append(assembled)
        if assembled:
            self.state.text_chunks.append(assembled)

        for block in tool_blocks:
            self._add_tool_row(block)

        # An assembled AssistantMessage marks a stable boundary. If
        # we've held the outer spinner this long, it's safe to open the
        # gutter now and let its final frame render the prose / tool
        # rows. Skip if there's no content at all (text-less phase
        # before a tool-use phase, e.g.).
        if assembled or tool_blocks:
            self._refresh_gutter_or_plain()

    def _on_user(self, msg: UserMessage) -> None:
        # Only tool-result content concerns us. String UserMessages are
        # echoes of the prompt and pre-existed in the transcript.
        if not isinstance(msg.content, list):
            return
        any_resolved = False
        for block in msg.content:
            if isinstance(block, ToolResultBlock | ServerToolResultBlock):
                self._resolve_tool_row(block)
                any_resolved = True
        if any_resolved:
            self._refresh_gutter_or_plain()
            # After tools resolve, expect the model to keep talking; if
            # no prose has streamed yet, fall back to "composing".
            if not self._all_text():
                self._set_outer_spinner(SpinnerState.COMPOSING, None)

    def _on_result(self, msg: ResultMessage) -> None:
        self.state.cost_usd = msg.total_cost_usd
        self.state.duration_ms = msg.duration_ms
        if msg.usage is not None and self.state.usage is None:
            self.state.usage = msg.usage
        if msg.stop_reason is not None and self.state.stop_reason is None:
            self.state.stop_reason = msg.stop_reason

    # -- block accessors ---------------------------------------------------

    def _trailing_text_segment(self) -> _TextSegment:
        """Get or create the text segment at the tail of `_blocks`.

        New text deltas always go into the *current* (trailing) text
        segment. When a tool group arrives in between, a fresh text
        segment is opened for any subsequent prose — that's what
        preserves the natural `text → tools → text` ordering.
        """
        if self._blocks and isinstance(self._blocks[-1], _TextSegment):
            return self._blocks[-1]
        segment = _TextSegment()
        self._blocks.append(segment)
        return segment

    def _trailing_tool_group(self) -> _ToolGroup:
        """Get or create the tool group at the tail of `_blocks`.

        Parallel tool calls in a single AssistantMessage share one
        group. A subsequent text-only AssistantMessage opens a fresh
        text segment after it, so any later tool call lands in its own
        new group rather than merging back.
        """
        if self._blocks and isinstance(self._blocks[-1], _ToolGroup):
            return self._blocks[-1]
        group = _ToolGroup()
        self._blocks.append(group)
        return group

    def _all_text(self) -> str:
        return "".join(seg.text for seg in self._blocks if isinstance(seg, _TextSegment))

    # -- text + tool mutation ---------------------------------------------

    def _append_text(self, delta: str) -> None:
        if self.plain:
            self.console.out(delta, end="")
            self._trailing_text_segment().chunks.append(delta)
            return
        self._trailing_text_segment().chunks.append(delta)
        # Don't open the gutter on the very first delta — a single
        # character followed by a long pause looks broken. Wait for a
        # paragraph break (or for the AssistantMessage to assemble) so
        # the user sees a stable chunk land at once. Until then the
        # outer "thinking…" spinner keeps the line.
        if self._gutter_live is not None:
            self._gutter_live.update(self._build_gutter())
            return
        if "\n\n" in self._all_text():
            self._open_gutter_if_needed()
            if self._gutter_live is not None:
                self._gutter_live.update(self._build_gutter())

    def _add_tool_row(self, block: ToolUseBlock | ServerToolUseBlock) -> None:
        row = _ToolRow(block=block)
        self._trailing_tool_group().rows.append(row)
        self._tool_index[block.id] = row
        if self.plain:
            label = tool_label(block).plain
            self.console.print(f"[tool: {label}]", markup=False, highlight=False)

    def _resolve_tool_row(self, result: ToolResultBlock | ServerToolResultBlock) -> None:
        row = self._tool_index.get(result.tool_use_id)
        if row is None or row.resolved:
            return
        row.resolved = True
        row.is_error = bool(getattr(result, "is_error", False))
        row.summary = summarize_tool_result(result)
        if self.plain:
            label = tool_label(row.block).plain
            mark = "✗" if row.is_error else "✓"
            self.console.print(
                f"[tool: {mark} {label}] {row.summary}", markup=False, highlight=False
            )

    # -- gutter Live -------------------------------------------------------

    def _open_gutter_if_needed(self) -> None:
        if self.plain or self._gutter_live is not None:
            return
        self._close_outer_live()
        # Print a blank line so the gutter has air above it (matches the
        # design — 1 blank line above the first assistant content).
        self.console.print()
        self._gutter_live = Live(
            self._build_gutter(),
            console=self.console,
            refresh_per_second=12,
            transient=False,
            vertical_overflow="visible",
        )
        self._gutter_live.__enter__()

    def _refresh_gutter_or_plain(self) -> None:
        if self.plain:
            return
        self._open_gutter_if_needed()
        if self._gutter_live is not None:
            self._gutter_live.update(self._build_gutter())

    def _build_gutter(self) -> Gutter:
        """Assemble the current gutter content from the ordered block list.

        Each text segment renders as Markdown; each tool group as a
        grouped card. Adjacent blocks are separated by a blank gutter
        line so prose and tool cards don't run into each other. The
        closing line (if set by `mark_error`/`mark_interrupted`) is
        appended last.
        """
        items: list[RenderableType] = []
        for block in self._blocks:
            if isinstance(block, _TextSegment):
                text = block.text
                if not text:
                    continue
                if items:
                    items.append(Text(""))
                prose, refs = _split_refs_trailer(text)
                if prose:
                    items.append(Markdown(prose))
                if refs is not None:
                    if prose:
                        items.append(Text(""))
                    items.append(_render_refs_block(refs))
            else:  # _ToolGroup
                if not block.rows:
                    continue
                if items:
                    items.append(Text(""))
                items.append(self._build_tool_panel(block.rows))
        if self._closing_line is not None:
            if items:
                items.append(Text(""))
            items.append(self._closing_line)
        if not items:
            # Should not happen — gutter only opens once there's content —
            # but defend against an empty Group.
            items.append(Text(""))
        return Gutter(Group(*items), bar_style=_BAR_STYLES[self._gutter_style])

    def _build_tool_panel(self, tool_rows: list[_ToolRow]) -> Panel:
        """Render the grouped `tool calls (N)` card.

        Each row is one line: spinner / ✓ / ✗ glyph + tool label + (for
        resolved rows) a result summary in dim text. The card border
        stays dim regardless of any individual row's error state.
        Rows are `no_wrap=True` + ellipsis-overflow so a long summary
        clips to `…` instead of forcing the panel to wrap onto two
        visual lines (which makes the card look like it has phantom
        extra rows).
        """
        running = sum(1 for r in tool_rows if not r.resolved)
        rows: list[RenderableType] = []
        for row in tool_rows:
            label = tool_label(row.block)
            if not row.resolved:
                # Animated row: spinner from Rich, prefixed onto the label.
                rows.append(Spinner("dots", text=Text.assemble(Text(" "), label), style="dim"))
                continue
            if row.is_error:
                glyph = Text("✗ ", style="bold red")
                summary_style = "red"
            else:
                glyph = Text("✓ ", style="bold green")
                summary_style = "dim"
            line = Text(no_wrap=True, overflow="ellipsis")
            line.append_text(glyph)
            line.append_text(label)
            line.append("  ", style="dim")
            line.append(row.summary, style=summary_style)
            rows.append(line)
        title = f"tool calls ({len(tool_rows)})"
        if running:
            title += " · running"
        return Panel(
            Group(*rows),
            title=title,
            title_align="left",
            border_style="dim",
            padding=(0, 1),
            expand=False,
        )

    def _close_gutter_live(self) -> None:
        if self._gutter_live is None:
            return
        # Final frame: include the closing line if one was set after the
        # last natural update.
        try:
            self._gutter_live.update(self._build_gutter())
            self._gutter_live.__exit__(None, None, None)
        finally:
            self._gutter_live = None
        # Print a blank line so there's breathing room between the
        # closed gutter and the next `▸` prompt.
        self.console.print()

    # -- outer spinner -----------------------------------------------------

    def _set_outer_spinner(self, state: SpinnerState, tool_name: str | None) -> None:
        if self.plain:
            return
        # Don't open the outer spinner if the gutter has already opened —
        # the gutter is the active display now.
        if self._gutter_live is not None:
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
        # If the gutter is open with unresolved tool rows, mark them
        # interrupted so the final frame doesn't freeze a spinner.
        all_rows = [r for b in self._blocks if isinstance(b, _ToolGroup) for r in b.rows]
        unresolved = [r for r in all_rows if not r.resolved]
        if unresolved:
            for row in unresolved:
                row.resolved = True
                row.is_error = False
                row.summary = "interrupted"
            # In plain mode, also emit the interrupted lines so the
            # transcript reflects the closing state.
            if self.plain:
                for row in unresolved:
                    label = tool_label(row.block).plain
                    self.console.print(
                        f"[tool: ⚠ {label}] interrupted",
                        markup=False,
                        highlight=False,
                    )
            if self._gutter_live is not None and not self.plain:
                self._gutter_live.update(self._build_gutter())
        self._close_gutter_live()
        self._close_outer_live()
        if self.plain and self._all_text():
            # Ensure plain-mode text ends on a newline before the next
            # turn starts.
            self.console.out("\n", end="")

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
