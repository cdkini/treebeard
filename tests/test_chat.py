"""Tests for `om chat` — REPL plumbing, transcript, and error handling."""

from __future__ import annotations

import json
import pathlib
import subprocess
from typing import Any

from claude_agent_sdk import ClaudeSDKError
from click.testing import CliRunner

from om.cli import cli
from tests.conftest import write_cfg


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

    transcript = vault / ".om" / "conversations" / "chat-20260507-142305.jsonl"
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
    convo_dir = vault / ".om" / "conversations"
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
    convo_dir = vault / ".om" / "conversations"
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

    transcript = vault / ".om" / "conversations" / "chat-20260507-142305.jsonl"
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
    assert ".om/conversations/chat-20260507-142305.jsonl" in log
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

    transcript = vault / ".om" / "conversations" / "chat-20260507-142305.jsonl"
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
    convo_dir = vault / ".om" / "conversations"
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
    """`om chat` should pin the SDK to the vault dir, expose only
    read-only tools, allow project-level `.claude/` (so a vault-local
    CLAUDE.md flows through), and override Claude Code's agent system
    prompt with a vault-aware one."""
    from claude_agent_sdk import ClaudeSDKClient

    from om.chat import ALLOWED_TOOLS, _make_client

    client = _make_client(vault, "sonnet")
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
    resolves into `<vault>/.om/archive/`. Regression: chat must never
    surface archived notes."""
    import asyncio

    from om.chat import _archive_guard_hook

    hook = _archive_guard_hook(vault)

    cases = [
        # Read with a vault-relative file path inside the archive.
        ("Read", {"file_path": ".om/archive/2026-05-01__old-note.md"}),
        # Read with an absolute path inside the archive.
        ("Read", {"file_path": str(vault / ".om" / "archive" / "x.md")}),
        # Glob targeting the archive via path.
        ("Glob", {"pattern": "*.md", "path": ".om/archive"}),
        # Glob targeting the archive via pattern.
        ("Glob", {"pattern": ".om/archive/**/*.md"}),
        # Grep targeting the archive via path.
        ("Grep", {"pattern": "TODO", "path": ".om/archive"}),
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
    from om.chat import _make_client

    client = _make_client(vault, "sonnet")
    options = client.options
    assert options.hooks is not None
    pre_tool_use = options.hooks.get("PreToolUse") or []
    assert pre_tool_use, "expected a PreToolUse hook matcher"
    matchers = {m.matcher for m in pre_tool_use}
    assert "Read|Glob|Grep" in matchers


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
    transcript = vault / ".om" / "conversations" / "chat-20260507-142305.jsonl"
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
    transcript = vault / ".om" / "conversations" / "chat-20260507-142305.jsonl"
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
    transcript = vault / ".om" / "conversations" / "chat-20260507-142305.jsonl"
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


def test_slash_dispatcher_bare_aliases_still_exit(
    runner: CliRunner,
    cfg_dir: pathlib.Path,
    vault: pathlib.Path,
    mock_claude_sdk: dict[str, Any],
    freeze_now: list[Any],
) -> None:
    """Regression on the dispatcher refactor: the bare aliases `exit`
    and `quit` (no leading slash) must still terminate the REPL
    without sending the literal string to the model."""
    del freeze_now
    write_cfg(cfg_dir, vault)
    for word in ("exit", "quit", "/quit"):
        mock_claude_sdk["queries"].clear()
        result = runner.invoke(cli, ["chat"], input=f"{word}\n")
        assert result.exit_code == 0, (word, result.output)
        assert mock_claude_sdk["queries"] == [], (word, mock_claude_sdk["queries"])
