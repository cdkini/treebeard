# omniscience

`om` — the omniscience CLI.

## Setup

Requires [uv](https://docs.astral.sh/uv/) and Python 3.12+.

```bash
make install
```

## Usage

```bash
uv run om --help
```

Or activate the venv and use `om` directly:

```bash
source .venv/bin/activate
om --help
```

## Development

```bash
make install   # uv sync
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
