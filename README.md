# omniscience

`om` — the omniscience CLI.

## Setup

Requires [uv](https://docs.astral.sh/uv/), Python 3.12+, [`fzf`](https://github.com/junegunn/fzf)
(`brew install fzf`), and [`ripgrep`](https://github.com/BurntSushi/ripgrep)
(`brew install ripgrep`, used by `om grep`).

For the `om find` preview pane you can pick one of
[`bat`](https://github.com/sharkdp/bat) (`brew install bat`,
syntax-highlighted source — the default),
[`glow`](https://github.com/charmbracelet/glow) (`brew install glow`,
rendered markdown), or `cat` (plain, always available). `om init` lets
you pick which one; the runtime falls through the list if your chosen
tool isn't installed.

```bash
make install
```

This syncs dependencies and installs `om` onto your `PATH` via `uv tool install --editable`,
so edits to `src/om/` take effect without reinstalling. `make uninstall` removes it.

## Usage

Bare `om` opens an interactive picker over the 20 most recently edited
notes (mtime sorted). Enter opens the highlighted note in your editor;
Ctrl-N creates a new note named after whatever you've typed.

`om find` is the same picker without the recent-only cap — it lists
every note in the vault. Pass `--limit N` to cap.

`om grep` runs ripgrep through fzf — type to search note contents,
Enter opens the matched note at the matched line.

`om chat` opens an interactive Claude REPL backed by the Claude Agent SDK.
It authenticates through the bundled `claude` CLI, which means it uses
your existing Claude Code login — no API key needed. Each session writes
a JSONL transcript to `<vault>/.om/conversations/chat-<UTC-timestamp>.jsonl`,
which the auto-commit hook lands in git on exit. Ctrl-D (or Ctrl-C) exits.

```bash
om                # picker (last 20)
om find           # picker (all notes)
om find --limit 5 # picker (last 5)
om grep           # fuzzy-search note contents
om chat           # interactive Claude REPL
om help           # show all subcommands
om note foo       # create or open foo.md
om daily          # today's daily note
```

Filenames are derived from the frontmatter `title:`. After every save
the file is renamed to match `slugify(title)` — the title is the source
of truth. An empty title falls back to `scratch-<timestamp>.md` and stays
that way until you give it a name. Daily-tagged notes are protected:
edits that would rename a daily off its date filename are reverted.

## Development

```bash
make sync      # uv sync (deps only, no global install)
make hooks     # one-time: install pre-commit hooks
make fmt       # auto-format + auto-fix
make lint      # ruff check + ruff format --check + basedpyright
make test      # pytest
```

## Adding a command

Drop a module into `src/om/commands/` that exports a top-level `command`
attribute (a `click.Command` or `click.Group`). It will be auto-registered.

```python
# src/om/commands/hello.py
import click

@click.command()
@click.argument("name")
def command(name: str) -> None:
    """Say hello."""
    click.echo(f"hello, {name}")
```

Then `om hello world` works with no further wiring.

## Layout

```
.
├── Makefile
├── pyproject.toml
├── src/om/
│   ├── __init__.py
│   ├── cli.py            # root click group + auto-registration
│   └── commands/         # drop subcommand modules here
└── tests/
```
