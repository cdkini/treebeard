# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

`om` is a personal note CLI: Click commands over a flat, git-synced markdown vault, plus a Claude Agent SDK-backed chat REPL. Python 3.12+, managed with `uv`. Single executable installed via `uv tool install`.

See `README.md` for user-facing setup and a working "add a command" example.

## Commands

All Python invocations go through `uv run` — never bare `python` / `pytest` / `ruff`.

```bash
make sync          # uv sync (deps only)
make install       # uv sync + uv tool install --editable .
make hooks         # one-time: install pre-commit (ruff only)
make fmt           # ruff format + ruff check --fix
make lint          # ruff check + ruff format --check + basedpyright
make test          # pytest with coverage (term + htmlcov/)
make uninstall     # uv tool uninstall omniscience
```

Single test:

```bash
uv run pytest tests/test_chat.py::test_basic_repl -xvs
```

## Architecture (high-level)

### Command auto-registration

`src/om/commands/__init__.py` iterates `pkgutil.iter_modules(__path__)` and yields any module-level `command` (a `click.Command` / `click.Group`). `cli.py` registers them in a loop at import time — there are no hard-coded subcommand imports. Drop `src/om/commands/foo.py` exporting `command` and `om foo` is live. Underscore-prefixed modules are skipped.

### `_on_close` post-edit + auto-commit hook

`cli()` registers `ctx.call_on_close(lambda: _on_close(ctx))`. On exit of every subcommand (including bare `om`, which dispatches to `find`):

1. **Post-edit sweep** (`_run_post_edit_hooks`) — runs `editor.apply_post_edit` on every dirty root-level `.md` reported by `git.changed_root_md_paths`. This catches both the file the subcommand opened and any sidetracks (`:e other.md`, wikilinks, `gf`). `post_edit.reconcile_filename` renames each file to `slugify(title)`; daily-tagged notes are exempt; `PostEditAbort` (collision, daily protection) is warned and the loop continues so the user's edit isn't lost.
2. **Auto-commit** — if `git.has_changes`, `git.commit_all` with subject `<subcommand>: <UTC-timestamp>`.
3. **Sync-warn** — if local commits ≥ `sync_warn_threshold` (default 10), nag to `om sync`.

Implications when adding a subcommand:
- Don't write per-command commit/post-edit logic; the hook owns it.
- The subcommand must set `ctx.obj["config_dir"]` if it accepts `--config-dir`, or the hook resolves the vault from the default config.
- If a subcommand legitimately should *not* trigger a commit, suppress at the hook (check `sub`), not by skipping the hook.
- Order matters: the sweep mutates files and bumps `updated_at`, and those changes must land in the same commit as the user's edits.

### Chat REPL

`src/om/chat.py` runs an async REPL via `claude_agent_sdk.ClaudeSDKClient`, which spawns the bundled `claude` CLI as a subprocess — no API key in code; auth uses the user's Claude Code login. Tool allowlist is hard-coded:

```python
ALLOWED_TOOLS = ("Read", "Glob", "Grep", "WebFetch", "WebSearch")
```

No `Bash` / `Write` / `Edit` — chat is a read-only assistant. The system prompt is loaded at runtime from `src/om/prompts/system_prompt.txt` (edit it there, not as a string literal). Transcripts append to `<vault>/.om/conversations/chat-<utc>.jsonl` and are landed in git by the same auto-commit hook.

## Tests

Pytest with heavy fixture isolation. See `tests/conftest.py` — prefer fixtures over `unittest.mock` for new tests. Key fixtures:

- `vault` — temp vault with `.om/` and `.git/` initialized
- `runner` — Click `CliRunner`
- `fake_editor` — queue of editor actions; monkeypatched into `om.editor`
- `freeze_now` / `freeze_today` — deterministic UTC timestamps and dates
- `mock_claude_sdk` — stub `ClaudeSDKClient` with configurable replies / model / usage / cost
- `write_cfg` — helper to seed `config.toml` for a test

Autouse fixtures sandbox `HOME` and the default config dir, so tests cannot touch the developer's real `~/.om/`.

## Before declaring work done

`make lint` and `make test` must both pass. The pre-commit hook only runs ruff (format + auto-fix), so basedpyright type errors are *not* caught at commit time — run `make lint` explicitly.
