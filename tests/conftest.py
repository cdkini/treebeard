"""Shared pytest fixtures."""

from __future__ import annotations

import pathlib
import subprocess
from collections.abc import Callable
from datetime import UTC, date, datetime

import pytest
from click.testing import CliRunner

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


def write_cfg(cfg_dir: pathlib.Path, vault: pathlib.Path, editor: str = "vim") -> None:
    """Pre-seed `cfg_dir/config.toml` for tests."""
    Config(vault=vault, editor=editor).save(str(cfg_dir))


@pytest.fixture
def fake_editor(monkeypatch: pytest.MonkeyPatch) -> list[EditorFake]:
    """Replace `editor.run_editor` with a queue of Python callables.

    Each callable receives `(editor_arg, path)` and may mutate the file.
    Append to the returned list to enqueue an action; an empty queue
    means "the user opened the editor and saved without changes."
    """
    queue: list[EditorFake] = []

    def fake_run_editor(editor: str, path: pathlib.Path) -> None:
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
    return queue


@pytest.fixture
def freeze_today(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(daily_cmd, "_today_local", lambda: FROZEN_TODAY)
