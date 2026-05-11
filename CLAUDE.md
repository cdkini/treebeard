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
make uninstall     # uv tool uninstall tb
```

Single test:

```bash
uv run pytest tests/test_chat.py::test_basic_repl -xvs
```

## Architecture (high-level)

### Command auto-registration

`src/treebeard/commands/__init__.py` iterates `pkgutil.iter_modules(__path__)` and yields any module-level `command` (a `click.Command` / `click.Group`). `cli.py` registers them in a loop at import time — there are no hard-coded subcommand imports. Drop `src/treebeard/commands/foo.py` exporting `command` and `tb foo` is live. Underscore-prefixed modules are skipped.

### `_on_close` post-edit + auto-commit hook

`cli()` registers `ctx.call_on_close(lambda: _on_close(ctx))`. The hook runs the post-edit sweep, then the indexer, then auto-commit, then a sync-warn check. See the README's "How `tb` changes your files" section for user-facing semantics; what matters when modifying `cli.py`:

- **Order is load-bearing.** The post-edit sweep mutates files (rename to `slugify(title)`, `updated_at` bump) and the indexer rewrites tag-index notes. Both must run *before* `git.commit_all`, so their changes land in the same commit as the user's edits. Don't reorder.
- **Indexer is isolated in its own `try`.** It's a convenience, not load-bearing — a broken indexer pass must not sink the auto-commit or the user's work. If you change indexer behavior, preserve that isolation.
- **The bare-`tb` skip is intentional.** `ctx.invoked_subcommand is None` (the help case) bypasses the hook entirely; don't move work outside that guard.
- **Per-subcommand suppression goes at the hook, not the subcommand.** If a subcommand legitimately should *not* trigger a commit, branch on `sub` inside `_on_close` rather than have the subcommand opt out.

Implications when adding a subcommand:
- Don't write per-command commit / post-edit logic; the hook owns it.
- The hook always resolves the vault from `~/.treebeard/config.toml` (`DEFAULT_CONFIG_DIR`); subcommands don't pass anything down.
- `PostEditAbort` (filename collision, daily-tag protection) is per-file: the loop must keep going so the user's edits aren't lost when one file can't be reconciled.

### Chat REPL

`src/treebeard/chat/` is a package that runs an async REPL via `claude_agent_sdk.ClaudeSDKClient`, which spawns the bundled `claude` CLI as a subprocess. User-facing behavior (auth, transcripts, slash commands, model knob) lives in the README. Internal layout:

- `session.py` — `run_repl` + REPL loop + header / summary panels. The only entry point exposed by `chat/__init__.py` (alongside `ALLOWED_TOOLS`).
- `client.py` — `make_client` (the SDK-construction seam tests monkeypatch), `ALLOWED_TOOLS`, `_archive_guard_hook`, `_build_system_prompt`. Anything that touches `claude_agent_sdk` directly lives here.
- `slash.py` — `SLASH_HANDLERS`, `/exit`, `/draft` (synthesis instruction + parse + handoff to `commands.note.create_named_note`).
- `prompt.py` — `prompt_toolkit` layer: `SlashCompleter`, `build_prompt_session`, `read_line`.
- `transcript.py` — JSONL append/path helpers. No SDK deps.
- `ui.py` — per-turn rendering (`TurnRenderer`, tool-card spinners, footer formatting).
- `prompts/system_prompt.txt` — loaded by `client._build_system_prompt` via `importlib.resources`. Edit the file, not Python.

Constraints when modifying:

- **`ALLOWED_TOOLS` is hard-coded** to `("Read", "Glob", "Grep", "WebFetch", "WebSearch")`. No `Bash` / `Write` / `Edit`. If you add a tool, justify it — chat is intentionally read-only.
- **Archive guard is a PreToolUse hook** that denies `Read`/`Glob`/`Grep` whose path resolves into `<vault>/.treebeard/archive/`. Any new path-bearing tool needs the same treatment.
- **`setting_sources=["project"]`** — the vault's `.claude/CLAUDE.md` and `.claude/` config flow through; the user's global Claude Code agent prompt and MCP servers are excluded by design.
- **Don't widen `chat/__init__.py`'s re-exports.** External callers see `run_repl` and `ALLOWED_TOOLS`. Tests reach into `treebeard.chat.client`, `treebeard.chat.slash`, etc. directly — that's the intended seam, not a leak.
- **Time goes through `treebeard.timefmt` as a module call.** Chat code does `from treebeard import timefmt` and calls `timefmt.now_utc()`, so a single `monkeypatch.setattr(timefmt_mod, "now_utc", ...)` covers every chat consumer. Don't reintroduce `from treebeard.timefmt import now_utc` inside the chat package.

### Startup performance

`tb` is a terminal-first tool — every command competes with `vim`, `fzf`, and a shell prompt for "feels instant." Cold-start latency from `tb <cmd>` to the first interactive frame (editor window, fzf picker, chat REPL) is a feature, not an afterthought.

Command auto-registration (`commands/__init__.py`) imports every module under `treebeard.commands` on every `tb` invocation. Anything those modules import at top level lands on the hot path — including heavyweights from sibling commands the user didn't ask for.

Rules:

- **Defer heavyweight third-party imports** in command modules. Anything that pulls in `claude_agent_sdk`, `prompt_toolkit`, `trafilatura`, `bs4`, `httpx`, or similarly chunky transitive trees belongs *inside* the handler function, not at module top. Rich's submodules (`rich.markdown`, `rich.live`, `rich.spinner`) are also lazy-load candidates when only one command needs them; `rich.console` is cheap and fine at module top (`ui.py` already uses it).
- **Don't eagerly import sibling commands' implementation modules** from a command module. `commands/chat.py` should not `from treebeard import chat` at top level — defer it into the handler. Same shape for any future command that fronts a heavy subsystem.
- **Tests must patch the source module**, not a stale alias on the command module. When `commands/foo.py` lazy-imports `Bar` inside its handler, `monkeypatch.setattr(commands.foo, "Bar", ...)` is silently a no-op — patch `treebeard.<source_module>.Bar` instead. The test files for `tb import web` / `tb import granola` already follow this pattern.
- **When adding a new heavy dependency**, profile before and after with `uv run python -X importtime -c "from treebeard.cli import cli" 2>&1 | tail -20`. The total at the bottom is the budget; today it sits around ~30ms for the `treebeard.cli` import (excluding the Python interpreter start). A new dep that pushes that above ~100ms needs a deferred-import plan in the same change.

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
