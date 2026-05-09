"""`tb chat` runtime — Claude Agent SDK REPL with JSONL transcript.

Authenticates via the bundled `claude` CLI binary that the Claude Agent SDK
spawns as a subprocess. That CLI uses your Claude Code login session, so a
Max subscription (or any active `claude login`) covers usage — no API key
required. Each invocation starts a fresh `ClaudeSDKClient`, which manages
conversation history inside the session. Every turn is appended to
`<vault>/.treebeard/conversations/chat-<UTC-timestamp>.jsonl` so the auto-commit
hook on CLI close picks it up.

Per-turn rendering lives in `treebeard.chat_ui.TurnRenderer`: streamed Markdown
body, bordered tool-call cards (running spinner → ✓/✗ summary as the
model invokes Read/Glob/Grep/WebFetch/WebSearch), and a dim footer line
with `model · tokens · cost · duration` after each reply. The renderer
falls back to a plain-text path when stdout isn't a TTY so pipes and
tests stay stable.
"""

from __future__ import annotations

import asyncio
import json
import pathlib
import re
import sys
from collections.abc import Awaitable, Callable, Iterable
from datetime import UTC, datetime
from enum import StrEnum
from importlib import resources
from typing import Any

from claude_agent_sdk import (
    ClaudeAgentOptions,
    ClaudeSDKClient,
    ClaudeSDKError,
    HookMatcher,
    ThinkingConfigDisabled,
)
from prompt_toolkit import PromptSession
from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.document import Document
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.history import InMemoryHistory
from rich.console import Console
from rich.panel import Panel
from rich.text import Text

from treebeard import ui, vault_layout
from treebeard.chat_ui import TurnRenderer
from treebeard.frontmatter import Source
from treebeard.timefmt import now_utc
from treebeard.ui import status_console

# Read-only tools we expose to the chat session. The user's vault is
# mounted as cwd, so `Read`/`Glob`/`Grep` operate against their notes.
# `Bash`, `Write`, `Edit`, `WebSearch`-style mutators are intentionally
# absent — chat must never modify the vault.
ALLOWED_TOOLS = ("Read", "Glob", "Grep", "WebFetch", "WebSearch")

# Vault-aware system prompt. The SDK's default is the full Claude Code
# agent persona — that's why Claude was trying to call MCP servers and
# emitting agent-style responses. Replace it with a focused assistant
# that knows it has read-only access to the user's notes vault.
#
# Loaded from `treebeard/prompts/system_prompt.txt` so edits don't require
# touching Python. Read once at import; the file is a package-shipped
# constant.
SYSTEM_PROMPT_BASE = (
    resources.files("treebeard")
    .joinpath("prompts/system_prompt.txt")
    .read_text(encoding="utf-8")
    .strip()
)


def _build_system_prompt(now: datetime) -> str:
    """Append today's UTC date to the base prompt.

    The vault CLAUDE.md tells the model to reason about dates from
    daily-note filenames (`YYYY-MM-DD.md`), but without an anchor for
    "today" it has to ask the user or guess. Pinning the date at session
    start lets phrases like "yesterday" / "last week" resolve directly.
    """
    today = now.astimezone(UTC).date().isoformat()
    return f"{SYSTEM_PROMPT_BASE}\n\nToday is {today} (UTC)."


# Glyph for the user-input prompt — matches `tb init`'s aesthetic.
PROMPT_GLYPH = "▸"


class _SlashOutcome(StrEnum):
    """What the REPL should do after a slash handler returns."""

    CONTINUE = "continue"
    BREAK = "break"


SlashHandler = Callable[
    ["ClaudeSDKClient", pathlib.Path, pathlib.Path, Console],
    Awaitable[_SlashOutcome],
]


# Slash commands surfaced in the header. Each entry is `(command, blurb)`;
# the rendered list is alphabetized by command. Keep this in sync with
# `SLASH_HANDLERS` (slash forms) when a new REPL command is added.
SLASH_COMMANDS: tuple[tuple[str, str], ...] = (
    ("/draft", "synthesize the conversation into a note, then exit"),
    ("/exit", "end the session"),
)


def conversation_path(vault: pathlib.Path, started_at: datetime) -> pathlib.Path:
    stamp = started_at.astimezone(UTC).strftime("%Y%m%d-%H%M%S")
    return vault_layout.conversations_dir(vault) / f"chat-{stamp}.jsonl"


def append_jsonl(path: pathlib.Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")


def _path_targets_archive(value: str, vault: pathlib.Path) -> bool:
    """Resolve `value` against `vault` and return True if it lands inside
    `.treebeard/archive/`. Accepts both vault-relative and absolute paths; an
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
    archive_root = vault_layout.archive_dir(vault).resolve(strict=False)
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
                            f"`{vault_layout.ARCHIVE_REL}` is off-limits to chat — these "
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
        system_prompt=_build_system_prompt(now_utc()),
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
        # it cuts several seconds off TTFT on short replies. With thinking
        # disabled, `effort` has nothing to modulate — leave it unset.
        thinking=ThinkingConfigDisabled(type="disabled"),
    )
    return ClaudeSDKClient(options=options)


def run_repl(vault: pathlib.Path, model: str) -> None:
    asyncio.run(_repl_async(vault, model))


async def _repl_async(vault: pathlib.Path, model: str) -> None:
    started_at = now_utc()
    transcript = conversation_path(vault, started_at)
    out = Console(highlight=False)

    _render_header(vault, transcript, model)
    session = _build_prompt_session()

    try:
        async with _make_client(vault, model) as client:
            while True:
                try:
                    user_text = await asyncio.to_thread(_read_line, session)
                except EOFError:
                    out.print()
                    break
                user_text = user_text.strip()
                if not user_text:
                    continue

                handler = SLASH_HANDLERS.get(user_text)
                if handler is not None:
                    try:
                        outcome = await handler(client, vault, transcript, out)
                    except ClaudeSDKError as exc:
                        ui.error(f"claude error: {exc}")
                        continue
                    except KeyboardInterrupt:
                        out.print()
                        ui.warn("interrupted")
                        continue
                    if outcome is _SlashOutcome.BREAK:
                        break
                    continue

                append_jsonl(
                    transcript,
                    {"ts": now_utc().isoformat(), "role": "user", "content": user_text},
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
    cmd_width = max(len(cmd) for cmd, _ in sorted_commands)
    for index, (cmd, blurb) in enumerate(sorted_commands):
        if index > 0:
            parts.append(("\n           ", "dim"))
        parts.append((f"{cmd.ljust(cmd_width)}  ", "white"))
        parts.append((blurb, "dim"))
    body = Text.assemble(*parts)
    status_console.print(
        Panel(
            body,
            title="[bold]tb chat[/bold]",
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


class _SlashCompleter(Completer):
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


def _build_prompt_session() -> PromptSession[str] | None:
    """Construct a `PromptSession` for interactive stdin, or `None`
    when stdin isn't a TTY.

    Returning `None` triggers the `input()` fallback in `_read_line` —
    the path tests rely on. `prompt_toolkit` would otherwise try to
    open `/dev/tty` directly and fight with `CliRunner`'s pipe.
    """
    if not sys.stdin.isatty():
        return None
    return PromptSession(
        completer=_SlashCompleter(SLASH_HANDLERS.keys()),
        history=InMemoryHistory(),
        complete_while_typing=False,
    )


def _read_line(session: PromptSession[str] | None) -> str:
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


async def _run_turn(
    client: ClaudeSDKClient,
    user_text: str,
    out: Console,
    transcript: pathlib.Path,
) -> str:
    """Drive one turn: stream the response through `TurnRenderer`, then
    log the assistant turn to the JSONL transcript.

    Returns the assembled assistant text. The REPL ignores it;
    `_handle_draft` uses it to parse the synthesis sentinel block.
    """
    await client.query(user_text)
    renderer = TurnRenderer(out)
    async for msg in client.receive_response():
        await renderer.consume(msg)
    summary = renderer.finalize()

    # Thin separator between turns (dim rule, no body).
    status_console.rule(style="dim")

    append_jsonl(
        transcript,
        {
            "ts": now_utc().isoformat(),
            "role": "assistant",
            "content": summary.text,
            "model": summary.model,
            "usage": summary.usage,
            "stop_reason": summary.stop_reason,
            "cost_usd": summary.cost_usd,
        },
    )
    return summary.text


# ---------------------------------------------------------------------------
# Slash command handlers
# ---------------------------------------------------------------------------


# Synthesis instruction injected as a plain user turn when the user
# types `/draft`. The model already has the conversation history; this
# prompt tells it to stop conversing and emit a structured draft block.
#
# The output uses a fenced code block with the language tag `draft-note`.
# This matters: bare sentinels like `<<<TITLE>>>` get treated as
# decorative markdown by some models and reproduced as-is in prose. A
# real fence is unambiguously a literal block — much harder to confuse.
# Inside the fence: a `title:` line, a `---` divider, then the markdown
# body.
SYNTHESIS_INSTRUCTION = """\
Synthesize the conversation we just had into a draft note.

Your entire reply must be ONE fenced code block tagged `draft-note`,
with no preamble before the opening fence and no text after the closing
fence. Inside the fence: a `title:` line, then a `---` divider on its
own line, then the markdown body.

Worked example (this is the literal shape, not the content to use):

```draft-note
title: Migration Cutover Notes
---
## Risks

Downtime during the cutover window is the dominant risk; rollback is
solid but rehearsed only twice.

## Mitigation

A staged cutover with feature flags reduces blast radius.
```

Title rules:
- Plain prose. No quotes, no trailing period. Slug-friendly.

Body rules:
- DO NOT include YAML frontmatter (`---title: ...---`) — that is added
  separately by the runtime.
- DO NOT include a leading H1 with the title — the title goes on the
  `title:` line, not in the body.
- Use H2/H3 (##/###) for internal structure.
- Voice: neutral / observational. Not first-person, not addressing me.
  Write about the topic, not to me.
- Length should match what we actually covered. A stub for thin chats,
  fuller prose for rich ones. Do not pad.
- If you read or cited any vault notes during the chat, end with a
  Refs: block per the vault CLAUDE.md conventions, but format each
  entry as an Obsidian wikilink: `[[slug|display name]]`.

If the conversation has barely any substance, emit a brief honest stub
anyway — never refuse, never ask follow-up questions.
"""


# Re-prompt sent after a parse failure. Single retry; if the second
# response is also malformed, we abort. Includes a literal worked
# example so the model can pattern-match instead of paraphrasing the
# rules from the first instruction.
SYNTHESIS_REPROMPT = """\
Your previous reply did not parse. Re-emit ONLY a fenced code block
tagged `draft-note`, exactly like this shape:

```draft-note
title: A Short Plain-Prose Title
---
## A heading

Body text here.
```

No preamble before the opening ```draft-note fence, no text after the
closing ``` fence, no commentary inside the block other than the
title line, the `---` divider, and the markdown body.
"""


# Matches the synthesis fence. The `re.DOTALL` flag lets `.*?` cross
# newlines; non-greedy match stops at the first closing ``` so a body
# that itself contains code fences would terminate parsing early — we
# accept that limitation since the body is markdown for a personal
# note, not a tutorial about fenced blocks.
_DRAFT_BLOCK_RE = re.compile(
    r"```draft-note\s*\n"
    r"title:\s*(?P<title>[^\n]+)\n"
    r"---\s*\n"
    r"(?P<body>.*?)\n?"
    r"```",
    re.DOTALL,
)


def _parse_draft_block(text: str) -> tuple[str, str] | None:
    """Pull `(title, body)` out of a fenced `draft-note` reply.

    Returns None when the fence is missing or either piece is empty —
    callers re-prompt once and abort on second failure.
    """
    match = _DRAFT_BLOCK_RE.search(text)
    if match is None:
        return None
    title = match.group("title").strip()
    body = match.group("body").strip()
    if not title or not body:
        return None
    # Trailing newline so the body, when serialized after frontmatter,
    # ends with a single \n the way other notes do.
    return title, body + "\n"


def _render_draft_parse_failure(reply: str, transcript: pathlib.Path) -> None:
    """Surface a parse failure as a Rich panel after the Live region tears
    down. The synthesis turn streams via `Live`, which on some terminals
    re-paints the whole reply per token — a single `ui.error` line gets
    buried in the noise. A panel printed to `status_console` lands cleanly
    below the stream and gives the user something actionable: what the
    model actually emitted (truncated) and where the audit trail lives.
    """
    snippet_limit = 500
    snippet = reply.strip()
    if len(snippet) > snippet_limit:
        snippet = snippet[:snippet_limit] + "\n…[truncated]"
    if not snippet:
        snippet = "(no content)"

    body = Text.assemble(
        ("the model did not emit a parseable `draft-note` fence. ", "white"),
        ("no note was written.\n\n", "white"),
        ("transcript ", "dim"),
        (f"{transcript}\n\n", "white"),
        ("model reply (truncated):\n", "dim"),
        (snippet, "white"),
    )
    status_console.print(
        Panel(
            body,
            title="[bold]could not parse draft[/bold]",
            border_style="red",
            expand=False,
        )
    )


def _unique_path(vault: pathlib.Path, slug: str) -> pathlib.Path:
    """Pick a non-colliding `vault/{slug}.md`, suffixing `-1`, `-2` etc.

    Collision policy is a chat-layer choice (don't clobber existing
    notes), distinct from `post_edit.slugify` which only knows about
    title→slug. Lives here so it stays close to its single caller.
    """
    base = vault / f"{slug}.md"
    if not base.exists():
        return base
    i = 1
    while True:
        candidate = vault / f"{slug}-{i}.md"
        if not candidate.exists():
            return candidate
        i += 1


async def _handle_exit(
    _client: ClaudeSDKClient,
    _vault: pathlib.Path,
    _transcript: pathlib.Path,
    _out: Console,
) -> _SlashOutcome:
    return _SlashOutcome.BREAK


async def _handle_draft(
    client: ClaudeSDKClient,
    vault: pathlib.Path,
    transcript: pathlib.Path,
    out: Console,
) -> _SlashOutcome:
    """Synthesize the conversation into a note, then end the session.

    Injects `SYNTHESIS_INSTRUCTION` as a user turn (the model already
    has the chat history; this just tells it what to do with it),
    parses the sentinel block, lands a `source: [user, llm]` note via
    `create_named_note`, and returns BREAK so the REPL exits.

    On parse failure: one re-prompt. If the second response is also
    malformed, write an error pointing at the transcript and exit
    without creating a note. The transcript is the audit trail.
    """
    # Imports here, not at module top, to avoid a circular import:
    # `commands.note` imports `treebeard.config`, which is fine at runtime but
    # would create a cycle if we eagerly imported it at chat.py load.
    from treebeard.commands.note import create_named_note
    from treebeard.config import load_config
    from treebeard.post_edit import PostEditAbort, scratch_filename, slugify

    append_jsonl(
        transcript,
        {
            "ts": now_utc().isoformat(),
            "role": "user",
            "content": SYNTHESIS_INSTRUCTION,
            "meta": {"synthetic": True},
        },
    )

    reply = await _run_turn(client, SYNTHESIS_INSTRUCTION, out, transcript)
    parsed = _parse_draft_block(reply)
    if parsed is None:
        append_jsonl(
            transcript,
            {
                "ts": now_utc().isoformat(),
                "role": "user",
                "content": SYNTHESIS_REPROMPT,
                "meta": {"synthetic": True},
            },
        )
        reply = await _run_turn(client, SYNTHESIS_REPROMPT, out, transcript)
        parsed = _parse_draft_block(reply)

    if parsed is None:
        _render_draft_parse_failure(reply, transcript)
        return _SlashOutcome.BREAK

    title, body = parsed
    try:
        slug = slugify(title)
    except PostEditAbort:
        slug = scratch_filename(now_utc()).removesuffix(".md")
        ui.warn(f"could not slugify title; falling back to {slug}")

    path = _unique_path(vault, slug)
    cfg = load_config()
    create_named_note(
        vault,
        path.stem,
        title,
        now_utc(),
        cfg.editor,
        body=body,
        keep_when_unchanged=True,
        sources=[Source.USER, Source.LLM],
    )

    append_jsonl(
        transcript,
        {
            "ts": now_utc().isoformat(),
            "role": "system",
            "event": "draft_written",
            "path": str(path),
        },
    )
    return _SlashOutcome.BREAK


SLASH_HANDLERS: dict[str, SlashHandler] = {
    "/exit": _handle_exit,
    "exit": _handle_exit,
    "/draft": _handle_draft,
}
