# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

`treebeard` is a personal note CLI: Click commands over a flat, git-synced markdown vault, plus a Claude Agent SDK-backed chat REPL. Python 3.12+, managed with `uv`. Single executable installed via `uv tool install`.

`README.md` is the canonical user-facing reference — feature surface, command flags, auto-behaviors, frontmatter schema, vault layout, config knobs. This file holds only what an agent modifying the code must know on top of that.

## Evaluating new asks

`treebeard` is a personal CLI for one user. "Useful" means *the user will use it* — there are no hypothetical users to design for. Push back accordingly.

Default to hard skeptic when an ask introduces a new command, new dependency, new abstraction, or a behavioral change to the vault / auto-commit / chat. Routine edits, bugfixes, and small tweaks skip the gauntlet.

When the user asks "should we do X?" or "what about Y?", lead with a verdict in 2–3 sentences: recommendation + the main tradeoff. Don't draft a plan or write code until the user agrees.

For non-trivial asks (new commands, new deps, new abstractions, behavioral changes to vault / auto-commit / chat, or anything where the right shape isn't obvious), probe thoroughly with `AskUserQuestion` *before* drafting a plan. Batch the questions in one tool call, cover the dimensions that would actually change the recommendation (usage frequency, scope, constraints, alternatives considered). Skip questions for trivial work — bugfixes, small tweaks, well-scoped edits — where you already have what you need. Use judgment; mechanical questioning on every ask is friction, not signal.

A feature clears the bar if it meets one of:
- The user will reach for it weekly, **or** it's high-leverage when used (recovery, migration, unblocking).
- It removes friction from a workflow the user already does — not a new thing they *might* do.
- It composes with existing primitives (vault / git / indexer / Click commands).

New subsystems start with a heavy negative prior. Overcomable, but the ask must explain why existing primitives can't carry the feature — and the session must say so out loud before agreeing.

Excitement is not a green light. If the user sounds committed but the ask looks shiny rather than useful, say so directly, then defer to their call.

Auto mode governs *how* to implement an agreed feature, not *whether* it should exist. Don't let auto mode bulldoze evaluation.

## Commands

All Python invocations go through `uv run` — never bare `python` / `pytest` / `ruff`.

```bash
make sync          # uv sync (deps only)
make install       # uv sync + uv tool install --editable .
make hooks         # one-time: install pre-commit (ruff only)
make fmt           # ruff format + ruff check --fix
make lint          # ruff check + ruff format --check + basedpyright
make test          # pytest with coverage (term + htmlcov/)
make ci            # lint + test (run before declaring work done)
make uninstall     # uv tool uninstall treebeard
```

Single test:

```bash
uv run pytest tests/test_chat.py::test_basic_repl -xvs
```

## Architecture (high-level)

### Command auto-registration

`src/treebeard/commands/__init__.py` iterates `pkgutil.iter_modules(__path__)` and yields any module-level `command` (a `click.Command` / `click.Group`). `cli.py` registers them in a loop at import time — there are no hard-coded subcommand imports. Drop `src/treebeard/commands/foo.py` exporting `command` and `treebeard foo` is live. Underscore-prefixed modules are skipped.

### `_on_close` post-edit + auto-commit hook

`cli()` registers `ctx.call_on_close(lambda: _on_close(ctx))`. The hook runs the post-edit sweep, then the indexer, then auto-commit, then a sync-warn check. See the README's "How `treebeard` changes your files" section for user-facing semantics; what matters when modifying `cli.py`:

- **Order is load-bearing.** The post-edit sweep mutates files (rename to `slugify(title)`, `updated_at` bump) and the indexer rewrites tag-index notes. Both must run *before* `git.commit_all`, so their changes land in the same commit as the user's edits. Don't reorder.
- **Indexer is isolated in its own `try`.** It's a convenience, not load-bearing — a broken indexer pass must not sink the auto-commit or the user's work. If you change indexer behavior, preserve that isolation.
- **The bare-`treebeard` skip is intentional.** `ctx.invoked_subcommand is None` (the help case) bypasses the hook entirely; don't move work outside that guard.
- **Per-subcommand suppression goes at the hook, not the subcommand.** If a subcommand legitimately should *not* trigger a commit, branch on `sub` inside `_on_close` rather than have the subcommand opt out.

Implications when adding a subcommand:
- Don't write per-command commit / post-edit logic; the hook owns it.
- The hook always resolves the vault from `~/.treebeard/config.toml` (`DEFAULT_CONFIG_DIR`); subcommands don't pass anything down.
- `PostEditAbort` (filename collision, daily-tag protection) is per-file: the loop must keep going so the user's edits aren't lost when one file can't be reconciled.

### Chat REPL

`src/treebeard/chat.py` runs an async REPL via `claude_agent_sdk.ClaudeSDKClient`, which spawns the bundled `claude` CLI as a subprocess. User-facing behavior (auth, transcripts, slash commands, model knob) lives in the README; the constraints when modifying this file:

- **`ALLOWED_TOOLS` is hard-coded** to `("Read", "Glob", "Grep", "WebFetch", "WebSearch")`. No `Bash` / `Write` / `Edit`. If you add a tool, justify it — chat is intentionally read-only.
- **System prompt is loaded from `src/treebeard/prompts/system_prompt.txt`**, not a Python string literal. Edit the file. Today's UTC date is appended at session start to anchor relative-date phrasing.
- **Archive guard is a PreToolUse hook** that denies `Read`/`Glob`/`Grep` whose path resolves into `<vault>/.treebeard/archive/`. Any new path-bearing tool needs the same treatment.
- **`setting_sources=["project"]`** — the vault's `.claude/CLAUDE.md` and `.claude/` config flow through; the user's global Claude Code agent prompt and MCP servers are excluded by design.

### `<vault>/.treebeard/` layout

`src/treebeard/vault_layout.py` is the single source of truth for what lives under `<vault>/.treebeard/` (the per-vault state dir, *not* `~/.treebeard/`, which is the user-level config dir handled in `treebeard.config`). Known sections today: `archive/` (soft-deleted notes, owned by `treebeard.archiver`) and `conversations/` (chat JSONL transcripts, owned by `treebeard.chat`).

When adding a new `.treebeard/` section, register it in `vault_layout` first and import the constructor from there — don't hardcode `.treebeard/<name>` elsewhere. Helpers in this module are pure path construction: no `mkdir`, no existence checks, no raises. Owners create their own subdirs lazily on first write (see `archiver.archive_paths`, `chat.append_jsonl`); be defensive and never assume a section exists.

## Tests

Pytest with heavy fixture isolation. See `tests/conftest.py` — prefer fixtures over `unittest.mock` for new tests. Key fixtures:

- `vault` — temp vault with `.treebeard/` and `.git/` initialized
- `runner` — Click `CliRunner`
- `fake_editor` — queue of editor actions; monkeypatched into `treebeard.editor`
- `freeze_now` / `freeze_today` — deterministic UTC timestamps and dates
- `mock_claude_sdk` — stub `ClaudeSDKClient` with configurable replies / model / usage / cost
- `write_cfg` — helper to seed `config.toml` for a test

Autouse fixtures sandbox `HOME` and the default config dir, so tests cannot touch the developer's real `~/.treebeard/`.

## Before declaring work done

`make ci` (lint + test) must pass. The pre-commit hook only runs ruff (format + auto-fix), so basedpyright type errors are *not* caught at commit time — `make ci` is the gate.

Total test coverage must stay **≥ 90%**, enforced by `--cov-fail-under=90` in the `test` target. If a change drops coverage below the threshold, add tests rather than lowering the gate.
