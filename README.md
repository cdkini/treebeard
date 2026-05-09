# omniscience

`om` — a personal-notes CLI over a flat, git-synced markdown vault, with a
Claude REPL that knows your notes.

## What it is

Your notes are plain markdown files in a git repo. `om` adds three things on
top of that:

- A small set of subcommands for capturing, finding, archiving, and syncing
  notes (`om note`, `om daily`, `om open`, `om grep`, `om archive`, `om sync`,
  `om import …`).
- Auto-mechanics that run on every command exit: filename is derived from
  the frontmatter title, daily-note dates are protected, per-tag index notes
  regenerate themselves, and the working tree auto-commits.
- `om chat` — an interactive Claude REPL with read-only access to the vault,
  using your existing Claude Code login (no API key).

The vault stays portable: clone it from any machine, `om init` to adopt it,
done. There's nothing in `om` you couldn't replicate with a hand-written
script — the value is in the wiring.

## Install

External dependencies (install once):

- [uv](https://docs.astral.sh/uv/) and Python 3.12+
- `git`, [`fzf`](https://github.com/junegunn/fzf) (`brew install fzf`),
  [`ripgrep`](https://github.com/BurntSushi/ripgrep) (`brew install
  ripgrep`) — checked at every CLI startup.
- Editor: `nvim` or `vim` (autodetected; `vim` is the fallback).
- Preview pane: one of [`bat`](https://github.com/sharkdp/bat) (default,
  syntax-highlighted), [`glow`](https://github.com/charmbracelet/glow)
  (rendered markdown), or `cat` (always present). The runtime falls
  through this list if your configured choice isn't installed.

Then:

```bash
make install     # uv sync + uv tool install --editable .
make uninstall   # remove
```

`uv tool install --editable` means edits to `src/om/` take effect without
reinstalling.

## Quickstart

```bash
om init ~/Notes               # scaffold (or adopt) a vault, write ~/.om/config.toml
om note hello                 # opens hello.md in your editor with frontmatter
                              # — on close: rename if you changed title, then auto-commit
om daily                      # opens YYYY-MM-DD.md, carrying forward unchecked TODOs
om open                       # fzf picker over the vault (Ctrl-N to create)
om grep                       # ripgrep through fzf, opens at the matched line
om chat                       # Claude REPL with read access to ~/Notes
om sync                       # pull --rebase + push
```

## Vault setup

`om init <path>` is non-interactive: the path is the only required input.
Everything else (editor, previewer, chat model, sync threshold) gets a
sensible default written into `~/.om/config.toml`; edit it later with
`om config`.

What `om init` does, depending on what's at `<path>`:

- **Empty or missing directory** — creates it, runs `git init`, writes a
  starter `<path>/.claude/CLAUDE.md` (the chat session's vault context),
  and makes a single `init: <UTC-timestamp>` commit.
- **Existing om vault** (a directory with both `.om/` and `.git/`) —
  records the path in `config.toml` and leaves the directory alone. Use
  this to adopt a vault cloned from another machine.
- **Anything else** (non-empty directory with no `.om/`, a regular file,
  a `.om/` without `.git/`) — refused with an error.

`om init` will not overwrite an already-configured `~/.om/config.toml`.

A vault is just a directory containing both `.om/` (om's metadata) and
`.git/` (history). Markdown notes live at the root — the layout is flat
on purpose.

## Commands

Grouped by what you're trying to do.

### Capture

**`om note [NAME]`** — create or open a markdown note.

- With `NAME`, opens `<vault>/<slugify(NAME)>.md` (created if missing,
  with frontmatter prefilled).
- Without `NAME`, opens an untitled `scratch-<UTC-timestamp>.md`. Add a
  title in the editor and on close it gets renamed; leave it untitled and
  it stays a scratch.

**`om daily`** — create or open today's daily note (`YYYY-MM-DD.md`).
Today's date comes from the local clock. The note is tagged `daily` (which
locks the filename — see Auto-behaviors). When creating a new daily, any
unchecked `- [ ]` TODOs from the most recent prior daily are carried
forward with a ` (from MM/DD)` provenance suffix.

**`om import granola [--since YYYY-MM-DD]`** — pull meeting notes from
[Granola](https://granola.ai). Defaults to the last 7 days. Each note
lands at `<vault>/granola-<date>-<slug>.md` with `source: import` and
`tags: [granola]`. Requires `granola_api_key` under `[secrets]` in
`config.toml`.

**`om import web <url>`** — fetch a web page and import it as a markdown
note (`<vault>/web-<date>-<slug>.md`).

### Find

**`om open [QUERY...] [--limit N]`** — fuzzy-pick a note from the vault.
With no QUERY, opens an interactive fzf picker (Enter opens the highlight;
**Ctrl-N** creates a new note named after whatever you've typed, or a fresh
scratch if empty). With QUERY, fuzzy-matches against vault filenames and
opens the top match (errors if nothing matches). `--limit N` caps the
candidate pool to the N most recently edited notes in either mode.

**`om grep`** — ripgrep through fzf. Each keystroke re-runs `rg` over the
vault. Enter opens the matched note at the matched line.

### Housekeeping

**`om archive`** — multi-select picker (Tab marks rows, Enter archives the
marked set, falling back to the focused row if nothing is marked). Each
file is moved to `<vault>/.om/archive/<UTC-timestamp>__<original>.md`. The
archive is excluded from `om open` / `om grep` and is hard-blocked for
`om chat`. Restore is a manual `mv` back to the vault root.

**`om sync`** — `git pull --rebase && git push` against the vault's
configured remote. Errors with a hint if no remote is set; add one with
`git remote add origin <url>` inside the vault.

**`om config`** — open `~/.om/config.toml` in your configured editor.

### Chat

**`om chat`** — see the [Chat](#chat) section below.

### Meta

- **`om`** (or `om help` / `om --help`) — list subcommands.

## How `om` changes your files

This section is load-bearing for trust. `om` will rename, rewrite, and
commit files behind your back — these are the rules.

### Title is the filename

Every note has YAML frontmatter; the `title` field is the source of truth
for the filename. After you save and close the editor, `om` renames the
file to `<slugify(title)>.md` (lowercased, runs of non-alphanumerics
collapsed to `-`). Rename `title:` to rename the file.

An empty title becomes `scratch-<UTC-timestamp>.md`; once a scratch note
gets a real title, it loses the `scratch-` prefix on the next save.

If the target name is already taken, the rename is aborted with a warning
and the file keeps its current name (your edits are preserved).

### Daily notes are protected

A note tagged `daily` keeps its `YYYY-MM-DD.md` filename. If you edit the
title in a daily and that would force a rename, the rename is refused with
a warning — the date in the filename is load-bearing for the carryover
lookup, so it isn't allowed to drift.

### Auto-commit on every command exit

After every subcommand exits, `om` stages all working-tree changes in the
vault and commits them with subject `<subcommand>: <UTC-timestamp>`. The
title-driven rename, the `updated_at` bump on edited notes, index updates,
and chat transcripts all land in the same commit as your edits.

Imported notes (`source: import`) are exempt from the `updated_at` bump so
the importer's idempotency works (re-running `om import` is a no-op when
nothing changed upstream).

### Auto-index per tag

When at least 3 non-index notes share a tag, `om` writes
`<vault>/<slug(tag)>.md` — an alphabetical wikilink list of every note
carrying that tag, marked with `tags: [index]`. When the count drops back
below 3 (e.g. after `om archive`), the now-orphaned index note is
auto-archived.

The pass is idempotent: if the desired body matches what's on disk, the
file is left alone. Hand-written notes whose filename collides with a tag
slug aren't overwritten — they're left alone with a warning.

Don't hand-edit `tags: [index]` notes; the next command will overwrite
them.

### Sync warning

If your local branch is `[sync] warn_threshold` commits or more ahead of
its remote (default 10), `om` nags you to run `om sync` after the command
completes.

## Chat

`om chat` opens an interactive Claude REPL with read-only access to the
vault.

- **Auth.** Spawns the bundled `claude` CLI as a subprocess and uses your
  existing Claude Code login. No API key in code or config; a Claude Max
  subscription covers usage.
- **Tools.** `Read`, `Glob`, `Grep` against the vault, plus `WebFetch` /
  `WebSearch`. No `Write`, no `Edit`, no `Bash` — chat cannot modify
  anything.
- **Archive guard.** A PreToolUse hook denies any `Read` / `Glob` / `Grep`
  whose path resolves into `.om/archive/`. Archived notes stay invisible
  to the model.
- **Vault context.** `<vault>/.claude/CLAUDE.md` is loaded as project
  context (scaffolded by `om init`). Edit it to teach the model about
  yourself, your conventions, and how you want it to cite. Today's UTC
  date is appended to the system prompt automatically so phrases like
  "yesterday" resolve.
- **Slash commands.** `/exit` and `/quit` end the session (Ctrl-D and
  Ctrl-C also work).
- **Model.** Set with `[chat] model` in `config.toml`. Accepts `sonnet`,
  `opus`, or any pinned id like `claude-sonnet-4-6`. Defaults to
  `sonnet`.
- **Transcripts.** Every turn is appended to
  `<vault>/.om/conversations/chat-<UTC-timestamp>.jsonl` with role,
  content, model, usage, and cost. The auto-commit hook lands the file in
  git on exit.
- **Session summary.** On exit, prints turn count, total tokens, and
  total cost (or `subscription` if the SDK didn't report a cost).

## Vault layout on disk

```
<vault>/
├── .git/                          # required; auto-committed by om
├── .om/
│   ├── archive/                   # soft-deleted notes (om archive)
│   │   └── 2026-05-08T14-22-09Z__some-old-note.md
│   └── conversations/             # om chat transcripts (JSONL)
│       └── chat-20260509-114433.jsonl
├── .claude/
│   └── CLAUDE.md                  # vault-specific chat context
├── 2026-05-08.md                  # daily note
├── granola-2026-05-07-assembly.md # imported (source: import)
├── granola.md                     # auto-generated index (tags: [index])
├── om-todos.md                    # hand-written note
└── scratch-2026-05-09t12-34-56.md # untitled scratch
```

The vault is flat — `om` puts every user-visible note at the root and
doesn't recurse into subdirectories. `om open` and `om grep` exclude
`.om/`, `.git/`, and `.claude/`.

## Configuration

`~/.om/config.toml`:

```toml
[vault]
path = "/Users/chetan/Notes"

[editor]
command   = "vim"   # options: nvim, vim
previewer = "bat"   # options: bat, glow, cat

[chat]
model = "sonnet"    # options: sonnet, opus (or any pinned id like claude-sonnet-4-6)

[sync]
warn_threshold = 10

[secrets]
granola_api_key = ""  # optional
```

- `[vault] path` — where your notes live. Set by `om init`; required for
  every other subcommand.
- `[editor] command` — binary to spawn for note edits. Falls back to
  `vim` if the configured editor isn't on `$PATH` and isn't a known
  alternative.
- `[editor] previewer` — preview pane in `om open` / `om archive`. Falls
  through to `cat` if your choice isn't installed.
- `[chat] model` — passed straight to the bundled `claude` CLI.
- `[sync] warn_threshold` — positive integer; trigger threshold for the
  unsynced-commits warning. Default 10.
- `[secrets] granola_api_key` — only consulted by `om import granola`.

Open the file with `om config`.

## Frontmatter schema

Every note carries a YAML block at the top:

```yaml
---
title: My Note
source: user
created_at: 2026-05-09T12:34:56Z
updated_at: 2026-05-09T12:34:56Z
tags: [project, idea]
---
```

- **`title`** — drives the filename via `slugify(title)`. Empty → scratch.
- **`source`** — `user` (you wrote it) or `import` (machine-generated; the
  `updated_at` bump is skipped so importers stay idempotent).
- **`created_at`**, **`updated_at`** — UTC ISO-8601, precision to the
  second.
- **`tags`** — inline list. Special values: `daily` (locks the filename
  to its `YYYY-MM-DD.md` form; carryover lookup depends on it), `index`
  (auto-generated by the indexer; don't hand-edit).

Imported notes also carry `import_source`, `import_id`, and `import_url`.
Unknown keys are preserved verbatim on rewrite.

## Development

```bash
make sync      # uv sync (deps only)
make install   # uv sync + uv tool install --editable .
make hooks     # one-time: install pre-commit (ruff)
make fmt       # ruff format + ruff check --fix
make lint      # ruff check + ruff format --check + basedpyright
make test      # pytest with coverage
make uninstall # uv tool uninstall omniscience
```

Use `make lint` and `make test` before declaring work done. The
pre-commit hook runs ruff only, so basedpyright errors aren't caught at
commit time.

### Adding a command

Drop a module into `src/om/commands/` that exports a top-level `command`
attribute (a `click.Command` or `click.Group`). It's auto-registered.

```python
# src/om/commands/hello.py
import click

@click.command()
@click.argument("name")
def command(name: str) -> None:
    """Say hello."""
    click.echo(f"hello, {name}")
```

Then `om hello world` works with no further wiring. The post-edit /
auto-commit hook runs after every subcommand — don't add per-command
commit logic.
