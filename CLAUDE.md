# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

`om` is a personal note CLI: Click commands over a flat, git-synced markdown vault, plus a Claude Agent SDK-backed chat REPL. Python 3.12+, managed with `uv`. Single executable installed via `uv tool install`.

`README.md` is the canonical user-facing reference — feature surface, command flags, auto-behaviors, frontmatter schema, vault layout, config knobs. This file holds only what an agent modifying the code must know on top of that.

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

`cli()` registers `ctx.call_on_close(lambda: _on_close(ctx))`. The hook runs the post-edit sweep, then the indexer, then auto-commit, then a sync-warn check. See the README's "How `om` changes your files" section for user-facing semantics; what matters when modifying `cli.py`:

- **Order is load-bearing.** The post-edit sweep mutates files (rename to `slugify(title)`, `updated_at` bump) and the indexer rewrites tag-index notes. Both must run *before* `git.commit_all`, so their changes land in the same commit as the user's edits. Don't reorder.
- **Indexer is isolated in its own `try`.** It's a convenience, not load-bearing — a broken indexer pass must not sink the auto-commit or the user's work. If you change indexer behavior, preserve that isolation.
- **The bare-`om` skip is intentional.** `ctx.invoked_subcommand is None` (the help case) bypasses the hook entirely; don't move work outside that guard.
- **Per-subcommand suppression goes at the hook, not the subcommand.** If a subcommand legitimately should *not* trigger a commit, branch on `sub` inside `_on_close` rather than have the subcommand opt out.

Implications when adding a subcommand:
- Don't write per-command commit / post-edit logic; the hook owns it.
- The subcommand must set `ctx.obj["config_dir"]` if it accepts `--config-dir`, or the hook resolves the vault from the default config.
- `PostEditAbort` (filename collision, daily-tag protection) is per-file: the loop must keep going so the user's edits aren't lost when one file can't be reconciled.

### Chat REPL

`src/om/chat.py` runs an async REPL via `claude_agent_sdk.ClaudeSDKClient`, which spawns the bundled `claude` CLI as a subprocess. User-facing behavior (auth, transcripts, slash commands, model knob) lives in the README; the constraints when modifying this file:

- **`ALLOWED_TOOLS` is hard-coded** to `("Read", "Glob", "Grep", "WebFetch", "WebSearch")`. No `Bash` / `Write` / `Edit`. If you add a tool, justify it — chat is intentionally read-only.
- **System prompt is loaded from `src/om/prompts/system_prompt.txt`**, not a Python string literal. Edit the file. Today's UTC date is appended at session start to anchor relative-date phrasing.
- **Archive guard is a PreToolUse hook** that denies `Read`/`Glob`/`Grep` whose path resolves into `<vault>/.om/archive/`. Any new path-bearing tool needs the same treatment.
- **`setting_sources=["project"]`** — the vault's `.claude/CLAUDE.md` and `.claude/` config flow through; the user's global Claude Code agent prompt and MCP servers are excluded by design.

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
