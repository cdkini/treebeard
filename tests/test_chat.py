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
    result = runner.invoke(cli, ["chat", "--config-dir", str(cfg_dir)], input="hi\n")
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
    runner.invoke(cli, ["chat", "--config-dir", str(cfg_dir)], input="ping\n")
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
    result = runner.invoke(cli, ["chat", "--config-dir", str(cfg_dir)], input="")
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
        ["chat", "--config-dir", str(cfg_dir)],
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
    runner.invoke(cli, ["chat", "--config-dir", str(cfg_dir)], input="hi\n")
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
        ["chat", "--config-dir", str(cfg_dir)],
        input="one\ntwo\n",
    )
    assert result.exit_code == 0, result.output
    assert mock_claude_sdk["queries"] == ["one", "two"]

    transcript = vault / ".om" / "conversations" / "chat-20260507-142305.jsonl"
    lines = [json.loads(line) for line in transcript.read_text().splitlines()]
    contents = [line["content"] for line in lines]
    assert contents == ["one", "A1", "two", "B2"]


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
