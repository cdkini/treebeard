"""Shared Click help-formatter overrides.

`RichGroup` swaps Click's plain "Commands:" block for a Rich `Table` so
the top-level `tb --help` and every subgroup (e.g. `tb import --help`)
render the same way.
"""

from __future__ import annotations

import io

import click


class RichGroup(click.Group):
    """`click.Group` that renders the commands section as a Rich table.

    The header (usage + description), options, and epilog stay on Click's
    formatter so flag handling stays canonical. Only the "Commands"
    section is replaced with a Rich `Table` keyed by command name.
    """

    def format_commands(self, ctx: click.Context, formatter: click.HelpFormatter) -> None:
        # Rich imports stay inside the help path so they don't land on the
        # hot startup path for `tb open`/`tb grep`/etc. See CLAUDE.md
        # "Startup performance".
        from rich.console import Console
        from rich.table import Table
        from rich.text import Text

        commands = [(name, self.get_command(ctx, name)) for name in self.list_commands(ctx)]
        commands = [(n, c) for n, c in commands if c is not None and not c.hidden]
        if not commands:
            return

        table = Table(
            show_header=False,
            box=None,
            padding=(0, 2),
            pad_edge=False,
        )
        table.add_column(style="bold cyan", no_wrap=True)
        table.add_column(style="white")
        for name, cmd in commands:
            help_text = cmd.get_short_help_str(limit=120) if cmd is not None else ""
            table.add_row(name, Text(help_text))

        # Render the Rich table to a string and feed it into Click's
        # formatter buffer so `--help` output remains a single contiguous
        # chunk (and `tb help` echoing `parent.get_help()` works).
        # `force_terminal=True` makes Rich emit ANSI escapes into the
        # StringIO buffer; `click.echo` then strips them when the final
        # sink isn't an interactive terminal, so pipes stay clean.
        buf = io.StringIO()
        Console(file=buf, force_terminal=True, width=formatter.width or 100).print(
            "\n[bold]Commands[/bold]"
        )
        Console(file=buf, force_terminal=True, width=formatter.width or 100).print(table)
        formatter.write(buf.getvalue())
