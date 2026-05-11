"""SDK client construction + PreToolUse archive guard.

This is the seam where `treebeard.chat` meets `claude_agent_sdk`. Tests
that need a stub SDK monkeypatch `make_client` at this module's path;
everything else (the REPL, slash handlers) goes through that factory so
the SDK is never imported in test paths.
"""

from __future__ import annotations

import pathlib
from datetime import UTC, datetime
from importlib import resources
from typing import Any

from claude_agent_sdk import (
    ClaudeAgentOptions,
    ClaudeSDKClient,
    HookMatcher,
    ThinkingConfigDisabled,
)

from treebeard import timefmt, vault_layout

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
# Loaded from `treebeard/chat/prompts/system_prompt.txt` so edits don't
# require touching Python. Read once at import; the file is a
# package-shipped constant.
SYSTEM_PROMPT_BASE = (
    resources.files("treebeard.chat")
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


def make_client(vault: pathlib.Path, model: str) -> ClaudeSDKClient:
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
        system_prompt=_build_system_prompt(timefmt.now_utc()),
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
