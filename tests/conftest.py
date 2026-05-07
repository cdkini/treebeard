"""Shared pytest fixtures."""

from __future__ import annotations

import pathlib
import subprocess
from collections.abc import Callable
from datetime import UTC, date, datetime
from typing import Any

import pytest
from click.testing import CliRunner

from om import chat as chat_mod
from om import config as config_mod
from om import editor as editor_mod
from om.commands import daily as daily_cmd
from om.commands import find as find_cmd
from om.commands import note as note_cmd
from om.config import Config

FROZEN_NOW = datetime(2026, 5, 7, 14, 23, 5, tzinfo=UTC)
FROZEN_LATER = datetime(2026, 5, 7, 15, 0, 0, tzinfo=UTC)
FROZEN_TODAY = date(2026, 5, 7)

EditorFake = Callable[[str, pathlib.Path], None]


@pytest.fixture(autouse=True)
def _isolated_git_global(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Point git at a throwaway global config so the auto-commit hook
    has identity available without reading the developer's real
    `~/.gitconfig`. Tests that exercise the init prompts also rely on
    this for predictable defaults."""
    fake = tmp_path_factory.mktemp("gitcfg") / "config"
    fake.write_text(
        "[user]\n\temail = test@example.com\n\tname = Test User\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(fake))
    monkeypatch.setenv("GIT_CONFIG_SYSTEM", "/dev/null")


@pytest.fixture(autouse=True)
def _sandbox_default_config_dir(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Redirect the default config dir away from `~/.om` so any test that
    invokes the CLI without an explicit `--config-dir` cannot read or
    write the developer's real vault."""
    sandbox = tmp_path_factory.mktemp("om-default-cfg")
    monkeypatch.setattr(config_mod, "DEFAULT_CONFIG_DIR", str(sandbox))


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def vault(tmp_path: pathlib.Path) -> pathlib.Path:
    v = tmp_path / "vault"
    (v / ".om").mkdir(parents=True)
    subprocess.run(["git", "init", "--quiet", "-b", "main"], cwd=v, check=True)
    return v


@pytest.fixture
def cfg_dir(tmp_path: pathlib.Path) -> pathlib.Path:
    d = tmp_path / "cfg"
    d.mkdir()
    return d


def write_cfg(
    cfg_dir: pathlib.Path,
    vault: pathlib.Path,
    editor: str = "vim",
    previewer: str = "bat",
    chat_model: str = "sonnet",
    sync_warn_threshold: int = 10,
) -> None:
    """Pre-seed `cfg_dir/config.toml` for tests."""
    Config(
        vault=vault,
        editor=editor,
        previewer=previewer,
        chat_model=chat_model,
        sync_warn_threshold=sync_warn_threshold,
    ).save(str(cfg_dir))


@pytest.fixture
def fake_editor(monkeypatch: pytest.MonkeyPatch) -> list[EditorFake]:
    """Replace `editor.run_editor` with a queue of Python callables.

    Each callable receives `(editor_arg, path)` and may mutate the file.
    Append to the returned list to enqueue an action; an empty queue
    means "the user opened the editor and saved without changes."
    """
    queue: list[EditorFake] = []

    def fake_run_editor(editor: str, path: pathlib.Path, *, start_line: int | None = None) -> None:
        del start_line
        if queue:
            queue.pop(0)(editor, path)

    monkeypatch.setattr(editor_mod, "run_editor", fake_run_editor)
    return queue


@pytest.fixture
def freeze_now(monkeypatch: pytest.MonkeyPatch) -> list[datetime]:
    """Freeze the clocks used by `note` and `editor`. Pass a list of
    timestamps; each call pops the front, repeating the last one."""
    queue: list[datetime] = [FROZEN_NOW]

    def fake_now() -> datetime:
        return queue.pop(0) if len(queue) > 1 else queue[0]

    monkeypatch.setattr(note_cmd, "_now_utc", fake_now)
    monkeypatch.setattr(editor_mod, "_now_utc", fake_now)
    monkeypatch.setattr(find_cmd, "_now_utc", fake_now)
    monkeypatch.setattr(chat_mod, "_now_utc", fake_now)
    return queue


@pytest.fixture
def freeze_today(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(daily_cmd, "_today_local", lambda: FROZEN_TODAY)


@pytest.fixture
def mock_claude_sdk(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Patch `om.chat._make_client` with a stub `ClaudeSDKClient` shape
    that the Claude Agent SDK exposes. Mutate the returned dict to
    customize per-test behavior:
      - `replies`: list[list[str]] — per-turn token chunks
      - `model`: str — AssistantMessage.model
      - `usage`: dict — AssistantMessage.usage and ResultMessage.usage
      - `stop_reason`: str — AssistantMessage.stop_reason
      - `cost_usd`: float — ResultMessage.total_cost_usd
      - `queries`: list[str] — user prompts the stub saw
      - `raise`: exception to raise from query() once, then auto-clear
    """
    from claude_agent_sdk import AssistantMessage, ResultMessage, TextBlock

    state: dict[str, Any] = {
        "replies": [["hello", " world"]],
        "model": "claude-sonnet-4-6",
        "usage": {"input_tokens": 3, "output_tokens": 2},
        "stop_reason": "end_turn",
        "cost_usd": 0.0,
        "queries": [],
        "raise": None,
    }

    class _FakeClient:
        async def __aenter__(self) -> _FakeClient:
            return self

        async def __aexit__(self, *_a: object) -> None:
            return None

        async def query(self, prompt: str) -> None:
            exc = state["raise"]
            if exc is not None:
                state["raise"] = None
                raise exc
            state["queries"].append(prompt)

        async def receive_response(self) -> Any:
            idx = len(state["queries"]) - 1
            chunks = state["replies"][idx] if idx < len(state["replies"]) else state["replies"][-1]
            yield AssistantMessage(
                content=[TextBlock(text=c) for c in chunks],
                model=state["model"],
                parent_tool_use_id=None,
                error=None,
                usage=state["usage"],
                message_id=None,
                stop_reason=state["stop_reason"],
                session_id=None,
                uuid=None,
            )
            yield ResultMessage(
                subtype="success",
                duration_ms=10,
                duration_api_ms=8,
                is_error=False,
                num_turns=1,
                session_id="sess",
                stop_reason=state["stop_reason"],
                total_cost_usd=state["cost_usd"],
                usage=state["usage"],
                result=None,
                structured_output=None,
                model_usage=None,
                permission_denials=None,
                deferred_tool_use=None,
                errors=None,
                api_error_status=None,
                uuid=None,
            )

    monkeypatch.setattr(chat_mod, "_make_client", lambda _vault, _model: _FakeClient())
    return state
