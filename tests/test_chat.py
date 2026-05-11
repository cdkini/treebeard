"""Tests for `tb chat` — REPL plumbing, transcript, and error handling."""

from __future__ import annotations

import json
import pathlib
import subprocess
from typing import Any

import pytest
from claude_agent_sdk import ClaudeSDKError
from click.testing import CliRunner

from tests.conftest import write_cfg
from treebeard.cli import cli


def test_uses_subscription_via_sdk(
    runner: CliRunner,
    cfg_dir: pathlib.Path,
    vault: pathlib.Path,
    mock_claude_sdk: dict[str, Any],
    freeze_now: list[Any],
) -> None:
    del freeze_now  # autouse-style: just need it to patch chat._now_utc
    write_cfg(cfg_dir, vault)
    result = runner.invoke(cli, ["chat"], input="hi\n")
    assert result.exit_code == 0, result.output

    transcript = vault / ".treebeard" / "conversations" / "chat-20260507-142305.jsonl"
    assert transcript.exists()
    lines = [json.loads(line) for line in transcript.read_text().splitlines()]
    assert lines[0]["role"] == "user"
    assert lines[0]["content"] == "hi"
    assert lines[1]["role"] == "assistant"
    assert lines[1]["content"] == "hello world"
    assert lines[1]["model"] == "claude-sonnet-4-6"
    assert lines[1]["usage"] == {"input_tokens": 3, "output_tokens": 2}
    assert lines[1]["stop_reason"] == "end_turn"
    assert lines[1]["cost_usd"] == 0.0
    assert mock_claude_sdk["queries"] == ["hi"]


def test_transcript_uses_frozen_timestamp(
    runner: CliRunner,
    cfg_dir: pathlib.Path,
    vault: pathlib.Path,
    mock_claude_sdk: dict[str, Any],
    freeze_now: list[Any],
) -> None:
    del freeze_now, mock_claude_sdk
    write_cfg(cfg_dir, vault)
    runner.invoke(cli, ["chat"], input="ping\n")
    convo_dir = vault / ".treebeard" / "conversations"
    assert convo_dir.is_dir()
    files = list(convo_dir.glob("chat-*.jsonl"))
    assert len(files) == 1
    assert files[0].name == "chat-20260507-142305.jsonl"


def test_eof_exits_cleanly(
    runner: CliRunner,
    cfg_dir: pathlib.Path,
    vault: pathlib.Path,
    mock_claude_sdk: dict[str, Any],
) -> None:
    del mock_claude_sdk
    write_cfg(cfg_dir, vault)
    result = runner.invoke(cli, ["chat"], input="")
    assert result.exit_code == 0, result.output
    convo_dir = vault / ".treebeard" / "conversations"
    assert not convo_dir.exists() or not list(convo_dir.glob("chat-*.jsonl"))


def test_sdk_error_keeps_repl_alive(
    runner: CliRunner,
    cfg_dir: pathlib.Path,
    vault: pathlib.Path,
    mock_claude_sdk: dict[str, Any],
    freeze_now: list[Any],
) -> None:
    del freeze_now
    write_cfg(cfg_dir, vault)
    # First query() raises; second succeeds (state["raise"] auto-clears).
    mock_claude_sdk["raise"] = ClaudeSDKError("boom")
    result = runner.invoke(
        cli,
        ["chat"],
        input="first\nsecond\n",
    )
    assert result.exit_code == 0, result.output
    assert "claude error" in result.output

    transcript = vault / ".treebeard" / "conversations" / "chat-20260507-142305.jsonl"
    lines = [json.loads(line) for line in transcript.read_text().splitlines()]
    roles = [line["role"] for line in lines]
    # Both user turns logged; only the second got an assistant reply.
    assert roles == ["user", "user", "assistant"]
    assert lines[0]["content"] == "first"
    assert lines[1]["content"] == "second"
    assert lines[2]["content"] == "hello world"


def test_auto_commit_picks_up_transcript(
    runner: CliRunner,
    cfg_dir: pathlib.Path,
    vault: pathlib.Path,
    mock_claude_sdk: dict[str, Any],
    freeze_now: list[Any],
) -> None:
    del freeze_now, mock_claude_sdk
    write_cfg(cfg_dir, vault)
    subprocess.run(
        [
            "git",
            "-C",
            str(vault),
            "commit",
            "--allow-empty",
            "-m",
            "init",
            "--quiet",
        ],
        check=True,
    )
    runner.invoke(cli, ["chat"], input="hi\n")
    log = subprocess.run(
        ["git", "-C", str(vault), "log", "--name-only", "--pretty=format:%s"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert ".treebeard/conversations/chat-20260507-142305.jsonl" in log
    assert "chat:" in log


def test_multi_turn_threads_through_one_client(
    runner: CliRunner,
    cfg_dir: pathlib.Path,
    vault: pathlib.Path,
    mock_claude_sdk: dict[str, Any],
    freeze_now: list[Any],
) -> None:
    """The SDK manages history — we just call query() per turn. Verify
    both user prompts reach the stub in order, in the same client."""
    del freeze_now
    write_cfg(cfg_dir, vault)
    mock_claude_sdk["replies"] = [["A1"], ["B2"]]

    result = runner.invoke(
        cli,
        ["chat"],
        input="one\ntwo\n",
    )
    assert result.exit_code == 0, result.output
    assert mock_claude_sdk["queries"] == ["one", "two"]

    transcript = vault / ".treebeard" / "conversations" / "chat-20260507-142305.jsonl"
    lines = [json.loads(line) for line in transcript.read_text().splitlines()]
    contents = [line["content"] for line in lines]
    assert contents == ["one", "A1", "two", "B2"]


def test_slash_exit_terminates_loop_without_calling_sdk(
    runner: CliRunner,
    cfg_dir: pathlib.Path,
    vault: pathlib.Path,
    mock_claude_sdk: dict[str, Any],
    freeze_now: list[Any],
) -> None:
    """`/exit` should break out of the REPL without sending the literal
    string to Claude — we don't want a session_summary turn that says
    `"the user wrote /exit, here's what that means..."`."""
    del freeze_now
    write_cfg(cfg_dir, vault)
    result = runner.invoke(cli, ["chat"], input="/exit\n")
    assert result.exit_code == 0, result.output
    assert mock_claude_sdk["queries"] == []
    convo_dir = vault / ".treebeard" / "conversations"
    assert not convo_dir.exists() or not list(convo_dir.glob("chat-*.jsonl"))


def test_slash_exit_after_real_turns_runs_summary(
    runner: CliRunner,
    cfg_dir: pathlib.Path,
    vault: pathlib.Path,
    mock_claude_sdk: dict[str, Any],
    freeze_now: list[Any],
) -> None:
    """`/exit` mid-session: prior turns reach Claude, then the loop ends
    cleanly and the summary lands."""
    del freeze_now
    write_cfg(cfg_dir, vault)
    mock_claude_sdk["replies"] = [["A1"], ["B2"]]
    result = runner.invoke(
        cli,
        ["chat"],
        input="one\ntwo\n/exit\n",
    )
    assert result.exit_code == 0, result.output
    assert mock_claude_sdk["queries"] == ["one", "two"]
    assert "session summary" in result.output
    assert "turns" in result.output
    assert "2" in result.output


def test_summary_shows_token_totals(
    runner: CliRunner,
    cfg_dir: pathlib.Path,
    vault: pathlib.Path,
    mock_claude_sdk: dict[str, Any],
    freeze_now: list[Any],
) -> None:
    """Token totals should sum across turns, sourced from the transcript."""
    del freeze_now
    write_cfg(cfg_dir, vault)
    mock_claude_sdk["usage"] = {"input_tokens": 11, "output_tokens": 7}
    mock_claude_sdk["replies"] = [["one"], ["two"]]
    result = runner.invoke(
        cli,
        ["chat"],
        input="hi\nhi\n/exit\n",
    )
    assert result.exit_code == 0, result.output
    # 11+11 in, 7+7 out
    assert "22 in / 14 out" in result.output


def test_summary_subscription_when_no_costs(
    runner: CliRunner,
    cfg_dir: pathlib.Path,
    vault: pathlib.Path,
    mock_claude_sdk: dict[str, Any],
    freeze_now: list[Any],
) -> None:
    """Subscription users have `cost_usd=None` on every ResultMessage —
    summary should label that 'subscription' rather than '$0.0000'."""
    del freeze_now
    write_cfg(cfg_dir, vault)
    mock_claude_sdk["cost_usd"] = None
    result = runner.invoke(
        cli,
        ["chat"],
        input="hi\n/exit\n",
    )
    assert result.exit_code == 0, result.output
    assert "subscription" in result.output
    assert "$" not in result.output


def test_summary_dollar_when_costs_present(
    runner: CliRunner,
    cfg_dir: pathlib.Path,
    vault: pathlib.Path,
    mock_claude_sdk: dict[str, Any],
    freeze_now: list[Any],
) -> None:
    del freeze_now
    write_cfg(cfg_dir, vault)
    mock_claude_sdk["cost_usd"] = 0.0123
    mock_claude_sdk["replies"] = [["one"], ["two"]]
    result = runner.invoke(
        cli,
        ["chat"],
        input="hi\nhi\n/exit\n",
    )
    assert result.exit_code == 0, result.output
    # 0.0123 + 0.0123 = 0.0246
    assert "$0.0246" in result.output


def test_summary_skipped_when_no_turns(
    runner: CliRunner,
    cfg_dir: pathlib.Path,
    vault: pathlib.Path,
    mock_claude_sdk: dict[str, Any],
) -> None:
    """Open chat, exit immediately — no panel, no spurious zeros."""
    del mock_claude_sdk
    write_cfg(cfg_dir, vault)
    result = runner.invoke(cli, ["chat"], input="/exit\n")
    assert result.exit_code == 0, result.output
    assert "session summary" not in result.output


def test_client_options_open_session_in_vault_with_readonly_tools(
    vault: pathlib.Path,
) -> None:
    """`tb chat` should pin the SDK to the vault dir, expose only
    read-only tools, allow project-level `.claude/` (so a vault-local
    CLAUDE.md flows through), and override Claude Code's agent system
    prompt with a vault-aware one."""
    from claude_agent_sdk import ClaudeSDKClient

    from treebeard.chat import ALLOWED_TOOLS
    from treebeard.chat.client import make_client

    client = make_client(vault, "sonnet")
    assert isinstance(client, ClaudeSDKClient)
    options = client.options
    # Read-only tools only — no Bash, Write, Edit, etc.
    assert set(options.tools or []) == set(ALLOWED_TOOLS)
    assert set(options.allowed_tools) == set(ALLOWED_TOOLS)
    for forbidden in ("Bash", "Write", "Edit"):
        assert forbidden not in (options.tools or [])
        assert forbidden not in options.allowed_tools
    # Vault is the working directory.
    assert options.cwd == str(vault)
    # Inherit project settings (CLAUDE.md, .claude/) but not the user's
    # global Claude Code config.
    assert options.setting_sources == ["project"]
    # No MCP servers leaking through from the user's global config.
    assert options.mcp_servers == {}
    assert options.strict_mcp_config is True
    assert options.skills == []
    assert options.include_partial_messages is True
    # System prompt is overridden and mentions the vault.
    assert isinstance(options.system_prompt, str)
    assert "vault" in options.system_prompt.lower()
    # Model alias is forwarded as-is — the `claude` CLI resolves it.
    assert options.model == "sonnet"


def test_archive_guard_denies_read_into_archive(vault: pathlib.Path) -> None:
    """The PreToolUse hook on Read/Glob/Grep should deny any path that
    resolves into `<vault>/.treebeard/archive/`. Regression: chat must never
    surface archived notes."""
    import asyncio

    from treebeard.chat.client import _archive_guard_hook

    hook = _archive_guard_hook(vault)

    cases = [
        # Read with a vault-relative file path inside the archive.
        ("Read", {"file_path": ".treebeard/archive/2026-05-01__old-note.md"}),
        # Read with an absolute path inside the archive.
        ("Read", {"file_path": str(vault / ".treebeard" / "archive" / "x.md")}),
        # Glob targeting the archive via path.
        ("Glob", {"pattern": "*.md", "path": ".treebeard/archive"}),
        # Glob targeting the archive via pattern.
        ("Glob", {"pattern": ".treebeard/archive/**/*.md"}),
        # Grep targeting the archive via path.
        ("Grep", {"pattern": "TODO", "path": ".treebeard/archive"}),
    ]
    for tool_name, tool_input in cases:
        result = asyncio.run(
            hook(
                {"tool_name": tool_name, "tool_input": tool_input},
                "tool-use-id",
                {"signal": None},
            )
        )
        assert result["hookSpecificOutput"]["permissionDecision"] == "deny", (
            tool_name,
            tool_input,
        )

    # Sanity: paths outside the archive must not be denied.
    benign = [
        ("Read", {"file_path": "2026-05-08.md"}),
        ("Glob", {"pattern": "*.md"}),
        ("Grep", {"pattern": "archive", "path": "."}),
    ]
    for tool_name, tool_input in benign:
        result = asyncio.run(
            hook(
                {"tool_name": tool_name, "tool_input": tool_input},
                "tool-use-id",
                {"signal": None},
            )
        )
        assert "hookSpecificOutput" not in result, (tool_name, tool_input)


def test_make_client_wires_archive_guard_hook(vault: pathlib.Path) -> None:
    """The client options should register a PreToolUse hook on
    Read|Glob|Grep so the archive guard runs before any read tool fires."""
    from treebeard.chat.client import make_client

    client = make_client(vault, "sonnet")
    options = client.options
    assert options.hooks is not None
    pre_tool_use = options.hooks.get("PreToolUse") or []
    assert pre_tool_use, "expected a PreToolUse hook matcher"
    matchers = {m.matcher for m in pre_tool_use}
    assert "Read|Glob|Grep" in matchers
    # PostToolUse scrubber on Glob|Grep to strip archived paths from
    # results (the PreToolUse guard only checks inputs).
    post_tool_use = options.hooks.get("PostToolUse") or []
    assert post_tool_use, "expected a PostToolUse hook matcher"
    post_matchers = {m.matcher for m in post_tool_use}
    assert "Glob|Grep" in post_matchers


def test_archive_scrubber_strips_archive_paths_from_glob_results(
    vault: pathlib.Path,
) -> None:
    """The PostToolUse scrubber should remove lines that reference
    paths under `.treebeard/archive/` from Glob/Grep results, leaving
    other matches intact. Regression: prior behavior leaked archived
    filenames into the chat because Glob results aren't input-checked."""
    import asyncio

    from treebeard.chat.client import _archive_scrubber_hook

    hook = _archive_scrubber_hook(vault)
    response = (
        "2026-05-11.md\n.treebeard/archive/2026-05-07T21-57-10Z__just-a-test.md\n2026-05-08.md\n"
    )
    result = asyncio.run(
        hook(
            {"tool_name": "Glob", "tool_response": response},
            "tool-use-id",
            {"signal": None},
        )
    )
    updated = result["hookSpecificOutput"]["updatedToolOutput"]
    assert "2026-05-11.md" in updated
    assert "2026-05-08.md" in updated
    assert ".treebeard/archive" not in updated


def test_archive_scrubber_handles_list_content_shape(vault: pathlib.Path) -> None:
    """Claude Code sometimes returns tool results as `[{type: text,
    text: ...}]` — scrubber must handle that shape too, not just bare
    strings."""
    import asyncio

    from treebeard.chat.client import _archive_scrubber_hook

    hook = _archive_scrubber_hook(vault)
    response = [
        {
            "type": "text",
            "text": ("2026-05-11.md\n.treebeard/archive/old.md\n2026-05-08.md\n"),
        }
    ]
    result = asyncio.run(
        hook(
            {"tool_name": "Glob", "tool_response": response},
            "tool-use-id",
            {"signal": None},
        )
    )
    updated = result["hookSpecificOutput"]["updatedToolOutput"]
    assert isinstance(updated, list)
    text = updated[0]["text"]
    assert "2026-05-11.md" in text
    assert "2026-05-08.md" in text
    assert ".treebeard/archive" not in text


def test_archive_scrubber_passthrough_when_no_archive_paths(vault: pathlib.Path) -> None:
    """If no lines reference the archive, the hook returns `{}` (no
    mutation) so the SDK passes the original response through
    unchanged."""
    import asyncio

    from treebeard.chat.client import _archive_scrubber_hook

    hook = _archive_scrubber_hook(vault)
    response = "2026-05-11.md\n2026-05-08.md\n"
    result = asyncio.run(
        hook(
            {"tool_name": "Glob", "tool_response": response},
            "tool-use-id",
            {"signal": None},
        )
    )
    assert result == {}


def test_archive_scrubber_returns_friendly_message_when_all_lines_archived(
    vault: pathlib.Path,
) -> None:
    """If every result line is archived, return a useful signal rather
    than empty output so the model knows what happened."""
    import asyncio

    from treebeard.chat.client import _archive_scrubber_hook

    hook = _archive_scrubber_hook(vault)
    response = ".treebeard/archive/a.md\n.treebeard/archive/b.md\n"
    result = asyncio.run(
        hook(
            {"tool_name": "Glob", "tool_response": response},
            "tool-use-id",
            {"signal": None},
        )
    )
    updated = result["hookSpecificOutput"]["updatedToolOutput"]
    assert "no matches" in updated
    assert ".treebeard/archive" in updated  # the explanation mentions it


# ---------------------------------------------------------------------------
# /draft — synthesis + handoff to vim
# ---------------------------------------------------------------------------


_VALID_DRAFT_BLOCK = (
    "```draft-note\n"
    "title: Migration Cutover Notes\n"
    "---\n"
    "## Risks\n\n"
    "Downtime during the cutover window.\n\n"
    "## Mitigation\n\n"
    "Rollback plan rehearsed twice.\n"
    "```\n"
)


def test_draft_synthesizes_and_writes_a_note(
    runner: CliRunner,
    cfg_dir: pathlib.Path,
    vault: pathlib.Path,
    mock_claude_sdk: dict[str, Any],
    fake_editor: list[Any],
    freeze_now: list[Any],
) -> None:
    """`/draft` mid-chat: model emits a sentinel block; runtime parses
    title/body, opens the editor, and lands a `source: [user, llm]`
    note in the vault."""
    del freeze_now, fake_editor
    write_cfg(cfg_dir, vault)
    # First reply is normal; second (response to /draft) is the block.
    mock_claude_sdk["replies"] = [["clarifying reply"], [_VALID_DRAFT_BLOCK]]

    result = runner.invoke(cli, ["chat"], input="some context\n/draft\n")
    assert result.exit_code == 0, result.output

    note_path = vault / "migration-cutover-notes.md"
    assert note_path.exists(), list(vault.iterdir())
    text = note_path.read_text(encoding="utf-8")
    assert "title: Migration Cutover Notes\n" in text
    assert "source: [user, llm]\n" in text
    assert "## Risks" in text
    assert "## Mitigation" in text


def test_draft_appends_draft_written_event_to_transcript(
    runner: CliRunner,
    cfg_dir: pathlib.Path,
    vault: pathlib.Path,
    mock_claude_sdk: dict[str, Any],
    fake_editor: list[Any],
    freeze_now: list[Any],
) -> None:
    del freeze_now, fake_editor
    write_cfg(cfg_dir, vault)
    mock_claude_sdk["replies"] = [["clarifying reply"], [_VALID_DRAFT_BLOCK]]

    runner.invoke(cli, ["chat"], input="hi\n/draft\n")
    transcript = vault / ".treebeard" / "conversations" / "chat-20260507-142305.jsonl"
    lines = [json.loads(line) for line in transcript.read_text().splitlines()]
    last = lines[-1]
    assert last["role"] == "system"
    assert last["event"] == "draft_written"
    assert last["path"].endswith("migration-cutover-notes.md")


def test_draft_marks_synthetic_user_turn_in_jsonl(
    runner: CliRunner,
    cfg_dir: pathlib.Path,
    vault: pathlib.Path,
    mock_claude_sdk: dict[str, Any],
    fake_editor: list[Any],
    freeze_now: list[Any],
) -> None:
    """The synthesis instruction is logged as a user turn (the model
    sees it that way), but tagged `meta.synthetic=true` so the
    transcript is honest about the injection."""
    del freeze_now, fake_editor
    write_cfg(cfg_dir, vault)
    mock_claude_sdk["replies"] = [[_VALID_DRAFT_BLOCK]]

    runner.invoke(cli, ["chat"], input="/draft\n")
    transcript = vault / ".treebeard" / "conversations" / "chat-20260507-142305.jsonl"
    lines = [json.loads(line) for line in transcript.read_text().splitlines()]
    synthetic_turns = [
        line
        for line in lines
        if line.get("role") == "user" and line.get("meta", {}).get("synthetic")
    ]
    assert len(synthetic_turns) == 1
    assert "Synthesize the conversation" in synthetic_turns[0]["content"]


def test_draft_terminates_session_after_writing(
    runner: CliRunner,
    cfg_dir: pathlib.Path,
    vault: pathlib.Path,
    mock_claude_sdk: dict[str, Any],
    fake_editor: list[Any],
    freeze_now: list[Any],
) -> None:
    """After `/draft` lands a note, the REPL must end — any further
    input on stdin should be ignored. We verify by feeding a third
    line that, if consumed, would show up in `queries`."""
    del freeze_now, fake_editor
    write_cfg(cfg_dir, vault)
    mock_claude_sdk["replies"] = [[_VALID_DRAFT_BLOCK]]

    result = runner.invoke(cli, ["chat"], input="/draft\nthis line should never reach the model\n")
    assert result.exit_code == 0, result.output
    # Only the synthesis instruction reached the SDK — not the third line.
    assert mock_claude_sdk["queries"] == [
        q for q in mock_claude_sdk["queries"] if "Synthesize the conversation" in q
    ]
    assert len(mock_claude_sdk["queries"]) == 1


def test_draft_keeps_note_when_editor_is_a_noop(
    runner: CliRunner,
    cfg_dir: pathlib.Path,
    vault: pathlib.Path,
    mock_claude_sdk: dict[str, Any],
    fake_editor: list[Any],
    freeze_now: list[Any],
) -> None:
    """Empty `fake_editor` queue = user saved without editing. The
    LLM-synthesized body is meaningful content, so we keep the file
    (`keep_when_unchanged=True`)."""
    del freeze_now, fake_editor
    write_cfg(cfg_dir, vault)
    mock_claude_sdk["replies"] = [[_VALID_DRAFT_BLOCK]]

    runner.invoke(cli, ["chat"], input="/draft\n")
    note_path = vault / "migration-cutover-notes.md"
    assert note_path.exists()


def test_draft_resolves_filename_collision_with_suffix(
    runner: CliRunner,
    cfg_dir: pathlib.Path,
    vault: pathlib.Path,
    mock_claude_sdk: dict[str, Any],
    fake_editor: list[Any],
    freeze_now: list[Any],
) -> None:
    """When `<slug>.md` already exists, the draft should land at
    `<slug>-1.md` rather than overwrite."""
    del freeze_now, fake_editor
    write_cfg(cfg_dir, vault)
    (vault / "migration-cutover-notes.md").write_text("preexisting\n", encoding="utf-8")
    mock_claude_sdk["replies"] = [[_VALID_DRAFT_BLOCK]]

    runner.invoke(cli, ["chat"], input="/draft\n")
    assert (vault / "migration-cutover-notes.md").read_text(encoding="utf-8") == "preexisting\n"
    assert (vault / "migration-cutover-notes-1.md").exists()


def test_draft_reprompts_once_then_succeeds_on_malformed_block(
    runner: CliRunner,
    cfg_dir: pathlib.Path,
    vault: pathlib.Path,
    mock_claude_sdk: dict[str, Any],
    fake_editor: list[Any],
    freeze_now: list[Any],
) -> None:
    """First synthesis reply is malformed; runtime injects a re-prompt;
    second reply is well-formed; note still lands."""
    del freeze_now, fake_editor
    write_cfg(cfg_dir, vault)
    mock_claude_sdk["replies"] = [
        ["sorry, here is your draft: lots of prose without a fence"],
        [_VALID_DRAFT_BLOCK],
    ]

    result = runner.invoke(cli, ["chat"], input="/draft\n")
    assert result.exit_code == 0, result.output
    assert (vault / "migration-cutover-notes.md").exists()
    # Two queries reached the SDK: the synthesis instruction + the re-prompt.
    assert len(mock_claude_sdk["queries"]) == 2
    assert "did not parse" in mock_claude_sdk["queries"][1]


def test_draft_gives_up_after_two_malformed_replies(
    runner: CliRunner,
    cfg_dir: pathlib.Path,
    vault: pathlib.Path,
    mock_claude_sdk: dict[str, Any],
    fake_editor: list[Any],
    freeze_now: list[Any],
) -> None:
    """Both synthesis replies malformed: error printed, no note written,
    REPL ends. The transcript still exists for audit."""
    del freeze_now, fake_editor
    write_cfg(cfg_dir, vault)
    mock_claude_sdk["replies"] = [["nope"], ["still nope"]]

    result = runner.invoke(cli, ["chat"], input="/draft\n")
    assert result.exit_code == 0, result.output
    md_files = list(vault.glob("*.md"))
    assert md_files == [], md_files
    # The failure surfaces as a Rich panel — the title is unique enough
    # to assert on without coupling to panel chrome.
    assert "could not parse draft" in result.output
    # And the panel includes the model's reply so the user can see why.
    assert "still nope" in result.output
    transcript = vault / ".treebeard" / "conversations" / "chat-20260507-142305.jsonl"
    assert transcript.exists()


def test_draft_falls_back_to_scratch_when_title_unslugifiable(
    runner: CliRunner,
    cfg_dir: pathlib.Path,
    vault: pathlib.Path,
    mock_claude_sdk: dict[str, Any],
    fake_editor: list[Any],
    freeze_now: list[Any],
) -> None:
    """Title that `slugify` rejects (e.g. all punctuation) → note
    lands at a `scratch-*.md` filename rather than failing."""
    del freeze_now, fake_editor
    write_cfg(cfg_dir, vault)
    mock_claude_sdk["replies"] = [["```draft-note\ntitle: !!!\n---\nSome body content.\n```\n"]]

    result = runner.invoke(cli, ["chat"], input="/draft\n")
    assert result.exit_code == 0, result.output
    scratch_files = list(vault.glob("scratch-*.md"))
    assert len(scratch_files) == 1, list(vault.iterdir())
    text = scratch_files[0].read_text(encoding="utf-8")
    assert "Some body content." in text
    assert "source: [user, llm]\n" in text


def test_slash_dispatcher_bare_alias_still_exits(
    runner: CliRunner,
    cfg_dir: pathlib.Path,
    vault: pathlib.Path,
    mock_claude_sdk: dict[str, Any],
    freeze_now: list[Any],
) -> None:
    """The bare `exit` alias (no leading slash) must terminate the REPL
    without sending the literal string to the model."""
    del freeze_now
    write_cfg(cfg_dir, vault)
    result = runner.invoke(cli, ["chat"], input="exit\n")
    assert result.exit_code == 0, result.output
    assert mock_claude_sdk["queries"] == []


# ---------------------------------------------------------------------------
# Per-turn UI: tool cards + footer (driven by TurnRenderer)
# ---------------------------------------------------------------------------


def test_tool_use_block_renders_tool_card(
    runner: CliRunner,
    cfg_dir: pathlib.Path,
    vault: pathlib.Path,
    mock_claude_sdk: dict[str, Any],
    freeze_now: list[Any],
) -> None:
    """A successful tool call should surface in the REPL output in
    plain mode as `[tool: Read foo.md]` (running) followed by
    `[tool: ✓ Read foo.md] <summary>` (resolved)."""
    del freeze_now
    write_cfg(cfg_dir, vault)
    mock_claude_sdk["tool_calls"] = [
        [
            {
                "name": "Read",
                "input": {"file_path": "foo.md"},
                "result": "First line of contents",
                "is_error": False,
            }
        ]
    ]
    result = runner.invoke(cli, ["chat"], input="hi\n")
    assert result.exit_code == 0, result.output
    assert "[tool: Read foo.md]" in result.output
    assert "[tool: ✓ Read foo.md]" in result.output
    assert "First line of contents" in result.output


def test_tool_error_renders_error_card(
    runner: CliRunner,
    cfg_dir: pathlib.Path,
    vault: pathlib.Path,
    mock_claude_sdk: dict[str, Any],
    freeze_now: list[Any],
) -> None:
    """`is_error=True` should flip the resolved card to ✗ + error snippet."""
    del freeze_now
    write_cfg(cfg_dir, vault)
    mock_claude_sdk["tool_calls"] = [
        [
            {
                "name": "Read",
                "input": {"file_path": "missing.md"},
                "result": "file not found",
                "is_error": True,
            }
        ]
    ]
    result = runner.invoke(cli, ["chat"], input="hi\n")
    assert result.exit_code == 0, result.output
    assert "[tool: ✗ Read missing.md]" in result.output
    assert "file not found" in result.output


def test_archive_guard_denial_surfaces_in_chat_ui(
    runner: CliRunner,
    cfg_dir: pathlib.Path,
    vault: pathlib.Path,
    mock_claude_sdk: dict[str, Any],
    freeze_now: list[Any],
) -> None:
    """When the archive-guard hook denies a Read, chat must surface
    that to the user — not silently swallow the deny."""
    del freeze_now
    write_cfg(cfg_dir, vault)
    deny_msg = (
        "`.treebeard/archive` is off-limits to chat — these notes were "
        "archived intentionally and must not be read or searched."
    )
    mock_claude_sdk["tool_calls"] = [
        [
            {
                "name": "Read",
                "input": {"file_path": ".treebeard/archive/old.md"},
                "result": deny_msg,
                "is_error": True,
            }
        ]
    ]
    result = runner.invoke(cli, ["chat"], input="hi\n")
    assert result.exit_code == 0, result.output
    assert "archive denied" in result.output


def test_no_per_turn_footer(
    runner: CliRunner,
    cfg_dir: pathlib.Path,
    vault: pathlib.Path,
    mock_claude_sdk: dict[str, Any],
    freeze_now: list[Any],
) -> None:
    """Per-turn cost/token footer was removed — the only place cost
    surfaces is the exit summary. The duration formatting (e.g. `4.2s`)
    was unique to the per-turn footer and never appears in the summary
    panel, so it's a clean regression marker."""
    del freeze_now
    write_cfg(cfg_dir, vault)
    mock_claude_sdk["cost_usd"] = 0.0123
    mock_claude_sdk["duration_ms"] = 4234
    mock_claude_sdk["usage"] = {"input_tokens": 1234, "output_tokens": 340}
    result = runner.invoke(cli, ["chat"], input="hi\n")
    assert result.exit_code == 0, result.output
    assert "hello world" in result.output
    # Per-turn duration was the footer's signature — must not leak.
    assert "4.2s" not in result.output


def test_session_summary_shows_subscription_when_cost_none(
    runner: CliRunner,
    cfg_dir: pathlib.Path,
    vault: pathlib.Path,
    mock_claude_sdk: dict[str, Any],
    freeze_now: list[Any],
) -> None:
    """Cost of None across a session should render `subscription` in
    the exit summary, not `$0.0000`."""
    del freeze_now
    write_cfg(cfg_dir, vault)
    mock_claude_sdk["cost_usd"] = None
    result = runner.invoke(cli, ["chat"], input="hi\n/exit\n")
    assert result.exit_code == 0, result.output
    assert "subscription" in result.output
    # No literal dollar sign should leak — the session summary prints
    # `subscription` for free / unmetered turns.
    assert "$" not in result.output


def test_tool_label_formatting_unit() -> None:
    """`tool_label` builds compact titles from each tool's input shape."""
    from claude_agent_sdk import ToolUseBlock

    from treebeard.chat.ui import tool_label

    cases: list[tuple[ToolUseBlock, str]] = [
        (ToolUseBlock(id="x", name="Read", input={"file_path": "notes/foo.md"}), "Read foo.md"),
        (ToolUseBlock(id="x", name="Glob", input={"pattern": "*.md"}), "Glob *.md"),
        (
            ToolUseBlock(id="x", name="Glob", input={"pattern": "*.md", "path": "daily"}),
            "Glob *.md in daily",
        ),
        (
            ToolUseBlock(id="x", name="Grep", input={"pattern": "TODO"}),
            'Grep "TODO"',
        ),
        (
            ToolUseBlock(id="x", name="Grep", input={"pattern": "TODO", "path": "notes"}),
            'Grep "TODO" in notes',
        ),
        (
            ToolUseBlock(id="x", name="WebFetch", input={"url": "https://anthropic.com/news"}),
            "WebFetch anthropic.com",
        ),
        (
            ToolUseBlock(id="x", name="WebSearch", input={"query": "claude code"}),
            'WebSearch "claude code"',
        ),
        # Unknown tool: bare name, no arg echo.
        (ToolUseBlock(id="x", name="Mystery", input={"arg": "value"}), "Mystery"),
        # Known tool with malformed input: degrade to bare name.
        (ToolUseBlock(id="x", name="Read", input={}), "Read"),
    ]
    for block, expected in cases:
        assert tool_label(block).plain == expected, (block.name, block.input)


def test_format_token_count_unit() -> None:
    """`format_token_count` collapses thousands tidily."""
    from treebeard.chat.ui import format_token_count

    assert format_token_count(0) == "0"
    assert format_token_count(42) == "42"
    assert format_token_count(999) == "999"
    assert format_token_count(1000) == "1k"
    assert format_token_count(1234) == "1.2k"
    assert format_token_count(12345) == "12.3k"


def test_format_duration_unit() -> None:
    """Sub-second durations stay in ms; otherwise 1 decimal of seconds."""
    from treebeard.chat.ui import format_duration

    assert format_duration(50) == "50ms"
    assert format_duration(999) == "999ms"
    assert format_duration(1000) == "1.0s"
    assert format_duration(4234) == "4.2s"


def test_summarize_tool_result_unit() -> None:
    """Result summaries: first line truncated, item counts for lists,
    archive denial surfaces with friendly prefix."""
    from claude_agent_sdk import ToolResultBlock

    from treebeard.chat.ui import summarize_tool_result

    # Short string content → first non-empty line.
    assert (
        summarize_tool_result(ToolResultBlock(tool_use_id="x", content="hello world"))
        == "hello world"
    )
    # Multi-line — pick first non-empty.
    assert (
        summarize_tool_result(ToolResultBlock(tool_use_id="x", content="\n\nfirst\nsecond"))
        == "first"
    )
    # Long line → truncated with ellipsis.
    long = "x" * 200
    summary = summarize_tool_result(ToolResultBlock(tool_use_id="x", content=long))
    assert summary.endswith("…")
    assert len(summary) <= 80
    # Archive denial substring → friendly prefix.
    deny = "`.treebeard/archive` is off-limits to chat — etc"
    assert summarize_tool_result(
        ToolResultBlock(tool_use_id="x", content=deny, is_error=True)
    ).startswith("archive denied:")
    # List of dicts with text → first text.
    assert (
        summarize_tool_result(
            ToolResultBlock(
                tool_use_id="x",
                content=[{"type": "text", "text": "matched 7 lines"}],
            )
        )
        == "matched 7 lines"
    )
    # List with no text entries → item count.
    assert (
        summarize_tool_result(
            ToolResultBlock(tool_use_id="x", content=[{"type": "image"}, {"type": "image"}])
        )
        == "2 items"
    )
    # Empty / None → done.
    assert summarize_tool_result(ToolResultBlock(tool_use_id="x", content=None)) == "done"


def test_spinner_text_for_unit() -> None:
    """Spinner labels reflect what the model is actually doing."""
    from treebeard.chat.ui import SpinnerState, spinner_text_for

    assert spinner_text_for(SpinnerState.AWAIT) == "thinking…"
    assert spinner_text_for(SpinnerState.COMPOSING) == "composing reply…"
    assert spinner_text_for(SpinnerState.TOOL, "Read") == "reading notes…"
    assert spinner_text_for(SpinnerState.TOOL, "Glob") == "searching paths…"
    assert spinner_text_for(SpinnerState.TOOL, "Grep") == "searching content…"
    assert spinner_text_for(SpinnerState.TOOL, "WebFetch") == "fetching…"
    assert spinner_text_for(SpinnerState.TOOL, "web_fetch") == "fetching…"
    assert spinner_text_for(SpinnerState.TOOL, "WebSearch") == "searching the web…"
    assert spinner_text_for(SpinnerState.TOOL, None) == "working…"
    assert spinner_text_for(SpinnerState.TOOL, "Custom") == "working with Custom…"


def test_turn_renderer_tty_mode_renders_gutter_and_tool_card(vault: pathlib.Path) -> None:
    """Direct test of `TurnRenderer` in TTY mode: drives a full turn
    with text + a tool call through the renderer and asserts on the
    captured Console output. `CliRunner` is always non-TTY, so this is
    the only place the TTY rendering path gets exercised.

    Assertions are structural — no per-turn footer (removed), tool
    label and result summary land inside the grouped card, the gutter
    bar character precedes content.
    """
    import asyncio
    import io

    from claude_agent_sdk import (
        AssistantMessage,
        ResultMessage,
        TextBlock,
        ToolResultBlock,
        ToolUseBlock,
        UserMessage,
    )
    from rich.console import Console

    from treebeard.chat.ui import TurnRenderer

    del vault

    buf = io.StringIO()
    out = Console(file=buf, force_terminal=True, width=120, legacy_windows=False)

    async def drive() -> None:
        renderer = TurnRenderer(out)
        # AssistantMessage with a tool call (opens the gutter).
        await renderer.consume(
            AssistantMessage(
                content=[
                    ToolUseBlock(id="t1", name="Read", input={"file_path": "foo.md"}),
                ],
                model="claude-sonnet-4-6",
                parent_tool_use_id=None,
                error=None,
                usage=None,
                message_id=None,
                stop_reason="tool_use",
                session_id=None,
                uuid=None,
            )
        )
        # UserMessage delivering the tool result.
        await renderer.consume(
            UserMessage(
                content=[ToolResultBlock(tool_use_id="t1", content="ok", is_error=False)],
                uuid=None,
                parent_tool_use_id=None,
                tool_use_result=None,
            )
        )
        # Final AssistantMessage with prose.
        await renderer.consume(
            AssistantMessage(
                content=[TextBlock(text="Done.")],
                model="claude-sonnet-4-6",
                parent_tool_use_id=None,
                error=None,
                usage={"input_tokens": 12, "output_tokens": 6},
                message_id=None,
                stop_reason="end_turn",
                session_id=None,
                uuid=None,
            )
        )
        await renderer.consume(
            ResultMessage(
                subtype="success",
                duration_ms=1500,
                duration_api_ms=8,
                is_error=False,
                num_turns=1,
                session_id="s",
                stop_reason="end_turn",
                total_cost_usd=0.0042,
                usage=None,
                result=None,
                structured_output=None,
                model_usage=None,
                permission_denials=None,
                deferred_tool_use=None,
                errors=None,
                api_error_status=None,
                uuid=None,
            )
        )
        summary = renderer.finalize()
        # Summary metadata survives into the JSONL-bound payload.
        assert "Done." in summary.text
        assert summary.cost_usd == 0.0042
        assert summary.duration_ms == 1500
        assert summary.model == "claude-sonnet-4-6"

    asyncio.run(drive())
    output = buf.getvalue()
    # Tool label and grouped-card title appear in the rendered frame.
    assert "Read" in output
    assert "foo.md" in output
    assert "tool calls" in output
    # The gutter bar character is the visible signal that the new
    # design is engaged.
    assert "┃" in output
    # Per-turn footer was removed — no model id leaking into the stream.
    assert "claude-sonnet-4-6" not in output
    assert "$0.0042" not in output
    assert "1.5s" not in output


def test_turn_renderer_finalize_is_idempotent() -> None:
    """Calling finalize() twice must not crash or print anything twice.
    Important so the Ctrl-C path can call it defensively from an
    except-branch even after a normal finalize landed."""
    import io

    from rich.console import Console

    from treebeard.chat.ui import TurnRenderer

    buf = io.StringIO()
    out = Console(file=buf, force_terminal=False, width=120)
    renderer = TurnRenderer(out)
    first = renderer.finalize()
    second = renderer.finalize()
    assert first == second
    # A finalized renderer with no content emits nothing.
    assert buf.getvalue() == ""


def test_turn_renderer_gutter_wraps_streamed_prose() -> None:
    """Streamed text deltas accumulate and render inside a magenta
    gutter once the buffer crosses a paragraph break (or finalize
    flushes a short reply). Smoke test of the new TTY rendering path."""
    import asyncio
    import io

    from claude_agent_sdk import AssistantMessage, StreamEvent, TextBlock
    from rich.console import Console

    from treebeard.chat.ui import TurnRenderer

    buf = io.StringIO()
    out = Console(file=buf, force_terminal=True, width=80, legacy_windows=False)
    renderer = TurnRenderer(out)

    async def drive() -> None:
        for chunk in ("Hello", " world"):
            await renderer.consume(
                StreamEvent(
                    uuid="u",
                    session_id="s",
                    event={
                        "type": "content_block_delta",
                        "delta": {"type": "text_delta", "text": chunk},
                    },
                    parent_tool_use_id=None,
                )
            )
        await renderer.consume(
            AssistantMessage(
                content=[TextBlock(text="Hello world")],
                model="claude-sonnet-4-6",
                parent_tool_use_id=None,
                error=None,
                usage={"input_tokens": 1, "output_tokens": 2},
                message_id=None,
                stop_reason="end_turn",
                session_id=None,
                uuid=None,
            )
        )

    asyncio.run(drive())
    renderer.finalize()
    output = buf.getvalue()
    # Gutter bar + prose both present.
    assert "┃" in output
    assert "Hello world" in output


def test_turn_renderer_grouped_tool_card_lists_all_tools() -> None:
    """When the model invokes multiple tools in one turn, the renderer
    builds a single `tool calls (N)` card with one row per call rather
    than N bordered panels."""
    import asyncio
    import io

    from claude_agent_sdk import AssistantMessage, ToolResultBlock, ToolUseBlock, UserMessage
    from rich.console import Console

    from treebeard.chat.ui import TurnRenderer

    buf = io.StringIO()
    out = Console(file=buf, force_terminal=True, width=120, legacy_windows=False)
    renderer = TurnRenderer(out)

    async def drive() -> None:
        await renderer.consume(
            AssistantMessage(
                content=[
                    ToolUseBlock(id="t1", name="Grep", input={"pattern": "auth"}),
                    ToolUseBlock(id="t2", name="Read", input={"file_path": "auth-notes.md"}),
                    ToolUseBlock(id="t3", name="Read", input={"file_path": "login-flow.md"}),
                ],
                model="claude-sonnet-4-6",
                parent_tool_use_id=None,
                error=None,
                usage=None,
                message_id=None,
                stop_reason="tool_use",
                session_id=None,
                uuid=None,
            )
        )
        await renderer.consume(
            UserMessage(
                content=[
                    ToolResultBlock(tool_use_id="t1", content="3 matches", is_error=False),
                    ToolResultBlock(tool_use_id="t2", content="42 lines", is_error=False),
                    ToolResultBlock(tool_use_id="t3", content="permission denied", is_error=True),
                ],
                uuid=None,
                parent_tool_use_id=None,
                tool_use_result=None,
            )
        )

    asyncio.run(drive())
    renderer.finalize()
    output = buf.getvalue()
    # Single grouped card with N=3, listing all three tool labels.
    assert "tool calls (3)" in output
    assert "Grep" in output
    assert "auth-notes.md" in output
    assert "login-flow.md" in output
    # Error row uses the red ✗ glyph (the error summary text leaks through).
    assert "permission denied" in output
    # Success rows resolved with ✓.
    assert "✓" in output
    assert "✗" in output


def test_draft_quiet_path_does_not_stream_fenced_reply(
    runner: CliRunner,
    cfg_dir: pathlib.Path,
    vault: pathlib.Path,
    mock_claude_sdk: dict[str, Any],
    fake_editor: list[Any],
    freeze_now: list[Any],
) -> None:
    """`/draft` runs the synthesis turn behind a spinner — the raw
    fenced `draft-note` block must not leak into the terminal output.
    The transcript still captures it for audit."""
    del freeze_now, fake_editor
    write_cfg(cfg_dir, vault)
    mock_claude_sdk["replies"] = [[_VALID_DRAFT_BLOCK]]

    result = runner.invoke(cli, ["chat"], input="/draft\n")
    assert result.exit_code == 0, result.output
    # The note still lands — synthesis ran end-to-end.
    assert (vault / "migration-cutover-notes.md").exists()
    # But the literal fence opener never appeared in the terminal.
    assert "```draft-note" not in result.output
    assert "title: Migration Cutover Notes" not in result.output
    # Transcript captures the assistant turn including the raw fence.
    transcript = vault / ".treebeard" / "conversations" / "chat-20260507-142305.jsonl"
    raw = transcript.read_text(encoding="utf-8")
    assert "```draft-note" in raw


def test_split_refs_trailer_pulls_trailing_block() -> None:
    """A trailing `Refs:` block with bullets is split off from the
    prose so the renderer can paint it tightly. Mid-text "Refs:" must
    not trigger."""
    from treebeard.chat.ui import _split_refs_trailer

    text = (
        "Two open TODOs today:\n\n"
        "1. fix lint\n"
        "2. review stack\n\n"
        "Refs:\n"
        "  - 2026-05-11.md\n"
        "  - 2026-05-08.md\n"
    )
    prose, refs = _split_refs_trailer(text)
    assert prose.endswith("review stack")
    assert refs == ["2026-05-11.md", "2026-05-08.md"]

    # Mid-text mention isn't a trailer.
    text2 = "Refs: are documented in the README. Here's a list:\n- a\n- b\n"
    prose2, refs2 = _split_refs_trailer(text2)
    assert prose2 == text2
    assert refs2 is None

    # No bullets after Refs: — fall back to passthrough.
    text3 = "Some prose.\n\nRefs:\n\n"
    _, refs3 = _split_refs_trailer(text3)
    assert refs3 is None


def test_render_refs_block_packs_label_and_bullets_tight() -> None:
    """The Refs renderable puts `Refs:` and each bullet on their own
    line with no blank between — that's the whole point of replacing
    Rich's Markdown list rendering for this trailer."""
    import io

    from rich.console import Console

    from treebeard.chat.ui import _render_refs_block

    buf = io.StringIO()
    out = Console(file=buf, force_terminal=False, width=80, no_color=True)
    out.print(_render_refs_block(["2026-05-11.md", "projects/migration.md"]))
    lines = buf.getvalue().splitlines()
    # First non-empty line is the label, immediately followed by bullets.
    assert lines[0].strip() == "Refs:"
    assert lines[1].strip().startswith("•")
    assert "2026-05-11.md" in lines[1]
    assert lines[2].strip().startswith("•")
    assert "projects/migration.md" in lines[2]
    # No blank line between label and bullets.
    assert all(line.strip() != "" for line in lines[:3])


def test_turn_renderer_mark_interrupted_plain_mode_emits_line() -> None:
    """`mark_interrupted` in non-TTY mode prints a single `⚠ interrupted`
    line to the console — covers the session loop's Ctrl-C path."""
    import io

    from rich.console import Console

    from treebeard.chat.ui import GutterStyle, TurnRenderer

    buf = io.StringIO()
    out = Console(file=buf, force_terminal=False, width=80)
    renderer = TurnRenderer(out)
    renderer.mark_interrupted()
    renderer.finalize()
    assert renderer._gutter_style is GutterStyle.INTERRUPTED
    assert "interrupted" in buf.getvalue()


def test_turn_renderer_mark_error_tty_mode_paints_red_gutter() -> None:
    """`mark_error` in TTY mode after the gutter has opened repaints
    the gutter red and surfaces the error inside it. Covers the path
    where a turn fails after some content has already streamed."""
    import asyncio
    import io

    from claude_agent_sdk import AssistantMessage, TextBlock
    from rich.console import Console

    from treebeard.chat.ui import GutterStyle, TurnRenderer

    buf = io.StringIO()
    out = Console(file=buf, force_terminal=True, width=80, legacy_windows=False)
    renderer = TurnRenderer(out)

    async def drive() -> None:
        # Push some content so the gutter opens.
        await renderer.consume(
            AssistantMessage(
                content=[TextBlock(text="Partial reply")],
                model="claude-sonnet-4-6",
                parent_tool_use_id=None,
                error=None,
                usage=None,
                message_id=None,
                stop_reason="end_turn",
                session_id=None,
                uuid=None,
            )
        )

    asyncio.run(drive())
    renderer.mark_error("claude error: boom")
    renderer.finalize()
    assert renderer._gutter_style is GutterStyle.ERROR
    output = buf.getvalue()
    assert "claude error: boom" in output


def test_turn_renderer_opens_gutter_on_paragraph_break() -> None:
    """In TTY mode, streamed deltas don't open the gutter until a
    paragraph break (`\\n\\n`) arrives — until then the outer
    `thinking…` spinner holds the line. Once the buffer crosses the
    break, the gutter opens with the accumulated prose."""
    import asyncio
    import io

    from claude_agent_sdk import StreamEvent
    from rich.console import Console

    from treebeard.chat.ui import TurnRenderer

    buf = io.StringIO()
    out = Console(file=buf, force_terminal=True, width=80, legacy_windows=False)
    renderer = TurnRenderer(out)

    async def drive() -> None:
        # First two deltas: no paragraph break → gutter stays closed.
        for chunk in ("Here is ", "the first part"):
            await renderer.consume(
                StreamEvent(
                    uuid="u",
                    session_id="s",
                    event={
                        "type": "content_block_delta",
                        "delta": {"type": "text_delta", "text": chunk},
                    },
                    parent_tool_use_id=None,
                )
            )
        assert renderer._gutter_live is None
        # Third delta carries the paragraph break → gutter opens.
        await renderer.consume(
            StreamEvent(
                uuid="u",
                session_id="s",
                event={
                    "type": "content_block_delta",
                    "delta": {"type": "text_delta", "text": ".\n\nMore prose."},
                },
                parent_tool_use_id=None,
            )
        )
        assert renderer._gutter_live is not None

    asyncio.run(drive())
    renderer.finalize()
    output = buf.getvalue()
    assert "Here is the first part" in output


def test_run_turn_quiet_records_transcript_with_metadata(
    runner: CliRunner,
    cfg_dir: pathlib.Path,
    vault: pathlib.Path,
    mock_claude_sdk: dict[str, Any],
    fake_editor: list[Any],
    freeze_now: list[Any],
) -> None:
    """`_run_turn_quiet` (used by `/draft`) writes a full assistant
    transcript record — content, model, usage, cost — even though the
    reply never streams to the terminal. Smoke test of the quiet
    driver's transcript handoff."""
    del freeze_now, fake_editor
    write_cfg(cfg_dir, vault)
    mock_claude_sdk["cost_usd"] = 0.0042
    mock_claude_sdk["usage"] = {"input_tokens": 11, "output_tokens": 7}
    mock_claude_sdk["replies"] = [[_VALID_DRAFT_BLOCK]]

    result = runner.invoke(cli, ["chat"], input="/draft\n")
    assert result.exit_code == 0, result.output

    transcript = vault / ".treebeard" / "conversations" / "chat-20260507-142305.jsonl"
    lines = [json.loads(line) for line in transcript.read_text().splitlines()]
    assistant_turns = [r for r in lines if r.get("role") == "assistant"]
    assert len(assistant_turns) == 1
    record = assistant_turns[0]
    # Full fenced reply landed in transcript content.
    assert "```draft-note" in record["content"]
    # Metadata captured by the quiet driver matches the stub's settings.
    assert record["model"] == "claude-sonnet-4-6"
    assert record["usage"] == {"input_tokens": 11, "output_tokens": 7}
    assert record["cost_usd"] == 0.0042


def test_slash_completer_suggests_only_when_buffer_starts_with_slash() -> None:
    """The completer is silent on prose and offers slash commands when
    the buffer starts with `/`. Suggestions come from `SLASH_HANDLERS`
    so adding a new handler automatically lights up in tab completion."""
    from prompt_toolkit.document import Document

    from treebeard.chat.prompt import SlashCompleter
    from treebeard.chat.slash import SLASH_HANDLERS

    completer = SlashCompleter(SLASH_HANDLERS.keys())

    # Empty buffer / prose: no suggestions.
    assert list(completer.get_completions(Document(""), None)) == []
    assert list(completer.get_completions(Document("hello"), None)) == []

    # `/` alone: every slash command in handler dict, alphabetized.
    suggestions = [c.text for c in completer.get_completions(Document("/"), None)]
    assert suggestions == sorted(c for c in SLASH_HANDLERS if c.startswith("/"))

    # Prefix narrows the list.
    suggestions = [c.text for c in completer.get_completions(Document("/d"), None)]
    assert suggestions == ["/draft"]

    # Bare `exit` alias (no leading slash) is not surfaced — it's a
    # usability fallback, not a menu item.
    suggestions = [c.text for c in completer.get_completions(Document("e"), None)]
    assert suggestions == []

    # Completion rewrites from cursor back to start of buffer so the
    # full command lands cleanly.
    completions = list(completer.get_completions(Document("/d"), None))
    assert completions[0].start_position == -2


def test_build_prompt_session_returns_none_when_stdin_not_a_tty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When stdin isn't a TTY (CliRunner, pipes), prompt_toolkit must
    not be initialised — the `input()` fallback path takes over."""
    from treebeard.chat.prompt import build_prompt_session

    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    assert build_prompt_session() is None


def test_build_prompt_session_constructs_when_stdin_is_a_tty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When stdin is a TTY, `_build_prompt_session` returns a real
    PromptSession wired up with the slash completer."""
    from prompt_toolkit import PromptSession

    from treebeard.chat.prompt import SlashCompleter, build_prompt_session

    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    session = build_prompt_session()
    assert isinstance(session, PromptSession)
    assert isinstance(session.completer, SlashCompleter)


def test_turn_renderer_finalize_marks_unresolved_tools_interrupted() -> None:
    """If a turn ends (Ctrl-C, SDK error, EOF) before tool results
    arrive, cleanup must close the gutter Live and mark unresolved
    rows so the terminal isn't left with a frozen spinner."""
    import asyncio
    import io

    from claude_agent_sdk import AssistantMessage, ToolUseBlock
    from rich.console import Console

    from treebeard.chat.ui import TurnRenderer

    buf = io.StringIO()
    out = Console(file=buf, force_terminal=True, width=120, legacy_windows=False)
    renderer = TurnRenderer(out)

    async def drive() -> None:
        await renderer.consume(
            AssistantMessage(
                content=[ToolUseBlock(id="t1", name="Read", input={"file_path": "foo.md"})],
                model="claude-sonnet-4-6",
                parent_tool_use_id=None,
                error=None,
                usage=None,
                message_id=None,
                stop_reason="tool_use",
                session_id=None,
                uuid=None,
            )
        )

    asyncio.run(drive())
    # Simulate Ctrl-C: finalize without ever delivering a UserMessage.
    renderer.finalize()
    # All Lives closed.
    assert renderer._gutter_live is None
    assert renderer._outer_live is None
    # All tool rows across all tool groups resolved with an
    # "interrupted" summary.
    from treebeard.chat.ui import _ToolGroup

    rows = [r for b in renderer._blocks if isinstance(b, _ToolGroup) for r in b.rows]
    assert rows
    for row in rows:
        assert row.resolved is True
        assert row.summary == "interrupted"
    output = buf.getvalue()
    # Terminal output mentions the interrupted state — either via the
    # row summary or the closing line.
    assert "interrupted" in output
