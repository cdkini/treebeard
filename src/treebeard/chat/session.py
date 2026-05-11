"""`tb chat` REPL driver.

Authenticates via the bundled `claude` CLI binary that the Claude Agent SDK
spawns as a subprocess. That CLI uses your Claude Code login session, so a
Max subscription (or any active `claude login`) covers usage — no API key
required. Each invocation starts a fresh `ClaudeSDKClient`, which manages
conversation history inside the session. Every turn is appended to
`<vault>/.treebeard/conversations/chat-<UTC-timestamp>.jsonl` so the
auto-commit hook on CLI close picks it up.

Per-turn rendering lives in `treebeard.chat.ui.TurnRenderer`: streamed
Markdown body, bordered tool-call cards (running spinner → ✓/✗ summary as
the model invokes Read/Glob/Grep/WebFetch/WebSearch), and a dim footer line
with `model · tokens · cost · duration` after each reply. The renderer
falls back to a plain-text path when stdout isn't a TTY so pipes and tests
stay stable.
"""

from __future__ import annotations

import asyncio
import pathlib
from typing import Any

from claude_agent_sdk import ClaudeSDKClient, ClaudeSDKError
from rich.console import Console
from rich.live import Live
from rich.panel import Panel
from rich.spinner import Spinner
from rich.text import Text

from treebeard import timefmt, ui
from treebeard.chat import client as client_mod
from treebeard.chat.prompt import build_prompt_session, read_line
from treebeard.chat.slash import SLASH_COMMANDS, SLASH_HANDLERS, SlashOutcome
from treebeard.chat.transcript import append_jsonl, conversation_path
from treebeard.chat.ui import TurnRenderer
from treebeard.ui import status_console


def run_repl(vault: pathlib.Path, model: str) -> None:
    asyncio.run(_repl_async(vault, model))


async def _repl_async(vault: pathlib.Path, model: str) -> None:
    started_at = timefmt.now_utc()
    transcript = conversation_path(vault, started_at)
    out = Console(highlight=False)

    _render_header(vault, model)
    session = build_prompt_session()

    try:
        async with client_mod.make_client(vault, model) as client:
            while True:
                try:
                    user_text = await asyncio.to_thread(read_line, session)
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
                    if outcome is SlashOutcome.BREAK:
                        break
                    continue

                append_jsonl(
                    transcript,
                    {
                        "ts": timefmt.now_utc().isoformat(),
                        "role": "user",
                        "content": user_text,
                    },
                )

                renderer = TurnRenderer(out)
                try:
                    await _run_turn(client, user_text, transcript, renderer)
                except ClaudeSDKError as exc:
                    # Paint the gutter red and append the error inside it
                    # so the message stays visually attached to the turn
                    # it belonged to.
                    renderer.mark_error(f"claude error: {exc}")
                    renderer.finalize()
                    continue
                except KeyboardInterrupt:
                    renderer.mark_interrupted()
                    renderer.finalize()
                    continue
    finally:
        _render_summary(transcript)


def _render_header(vault: pathlib.Path, model: str) -> None:
    """Render the compact chat-session header.

    Two rows of `vault` / `model` followed by the slash commands. The
    transcript path is no longer displayed — it's predictable from the
    vault path and the auto-commit hook picks it up automatically.
    Vault is tilde-shortened so the header stays narrow on screens with
    a long `$HOME`.
    """
    vault_display = _tilde_path(vault)
    parts: list[tuple[str, str]] = [
        ("vault  ", "dim"),
        (f"{vault_display}\n", "white"),
        ("model  ", "dim"),
        (f"{model}\n", "white"),
    ]
    sorted_commands = sorted(SLASH_COMMANDS, key=lambda pair: pair[0])
    cmd_width = max(len(cmd) for cmd, _ in sorted_commands)
    for index, (cmd, blurb) in enumerate(sorted_commands):
        if index > 0:
            parts.append(("\n", "dim"))
        parts.append((f"{cmd.ljust(cmd_width)}  ", "white"))
        parts.append((blurb, "dim"))
    body = Text.assemble(*parts)
    status_console.print(
        Panel(
            body,
            title="[bold]tb chat[/bold]",
            border_style="cyan",
            expand=False,
        )
    )


def _tilde_path(path: pathlib.Path) -> str:
    """Render a path with `$HOME` collapsed to `~`. Falls back to the
    full string if the path isn't under `$HOME`."""
    try:
        home = pathlib.Path.home()
        rel = path.resolve().relative_to(home.resolve())
        return f"~/{rel}" if str(rel) != "." else "~"
    except (ValueError, OSError):
        return str(path)


def _render_summary(transcript: pathlib.Path) -> None:
    """Print a compact session summary by reading back the transcript.

    The JSONL is the source of truth — each assistant record carries the
    `usage` dict and `cost_usd` for that turn, so we don't need to track
    running totals in memory. Silent no-op if the transcript is missing
    or has no assistant turns (e.g. the user opened chat and exited
    immediately, or every turn errored before producing a reply).
    """
    import json

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


async def _run_turn_quiet(
    client: ClaudeSDKClient,
    user_text: str,
    transcript: pathlib.Path,
    spinner_label: str,
) -> str:
    """Drive one turn with the response hidden behind a spinner.

    Used by `/draft`: the model emits a fenced `draft-note` block that
    is implementation detail to the user. We don't want to stream that
    fence into the terminal; we just want the assembled text back so
    the caller can parse it. The transcript still receives the assistant
    turn record (model, usage, cost, raw content) so the audit trail is
    unchanged.

    Returns the assembled assistant text.
    """
    await client.query(user_text)
    text_chunks: list[str] = []
    model: str | None = None
    usage: dict[str, Any] | None = None
    stop_reason: str | None = None
    cost_usd: float | None = None
    duration_ms: int | None = None

    # Lazy SDK imports happen at call site since these are already loaded by
    # the caller's stack — keeping them local to the function only adds noise.
    from claude_agent_sdk import AssistantMessage, ResultMessage, TextBlock

    spinner: Live | None = None
    if status_console.is_terminal:
        spinner = Live(
            Spinner("dots", text=Text(spinner_label, style="dim")),
            console=status_console,
            refresh_per_second=12,
            transient=True,
        )
        spinner.__enter__()
    try:
        async for msg in client.receive_response():
            if isinstance(msg, AssistantMessage):
                if msg.model:
                    model = msg.model
                if msg.usage is not None:
                    usage = msg.usage
                if msg.stop_reason is not None:
                    stop_reason = msg.stop_reason
                for block in msg.content:
                    if isinstance(block, TextBlock):
                        text_chunks.append(block.text)
            elif isinstance(msg, ResultMessage):
                cost_usd = msg.total_cost_usd
                duration_ms = msg.duration_ms
                if msg.usage is not None and usage is None:
                    usage = msg.usage
                if msg.stop_reason is not None and stop_reason is None:
                    stop_reason = msg.stop_reason
    finally:
        if spinner is not None:
            spinner.__exit__(None, None, None)

    text = "".join(text_chunks)
    append_jsonl(
        transcript,
        {
            "ts": timefmt.now_utc().isoformat(),
            "role": "assistant",
            "content": text,
            "model": model,
            "usage": usage,
            "stop_reason": stop_reason,
            "cost_usd": cost_usd,
            "duration_ms": duration_ms,
        },
    )
    return text


async def _run_turn(
    client: ClaudeSDKClient,
    user_text: str,
    transcript: pathlib.Path,
    renderer: TurnRenderer,
) -> str:
    """Drive one turn: stream the response through `renderer`, then log
    the assistant turn to the JSONL transcript.

    Returns the assembled assistant text. The REPL ignores it;
    `slash._handle_draft` uses it to parse the synthesis sentinel block.
    The caller owns the renderer's lifecycle — passing it in lets the
    session loop paint a red/yellow gutter on errors/interrupts before
    finalizing.
    """
    await client.query(user_text)
    async for msg in client.receive_response():
        await renderer.consume(msg)
    summary = renderer.finalize()

    append_jsonl(
        transcript,
        {
            "ts": timefmt.now_utc().isoformat(),
            "role": "assistant",
            "content": summary.text,
            "model": summary.model,
            "usage": summary.usage,
            "stop_reason": summary.stop_reason,
            "cost_usd": summary.cost_usd,
        },
    )
    return summary.text
