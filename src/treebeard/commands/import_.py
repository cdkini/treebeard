"""`tb import <integration>` — pull notes/artifacts from external integrations.

Group is extensible: add a new integration by registering a subcommand on
`command` (e.g. `@command.command("foo")`). Integrations themselves live
in `treebeard.importers` and implement the `Importer` Protocol; the click
handler is just glue.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import click

from treebeard.cli_help import RichGroup
from treebeard.config import load_config
from treebeard.ui import TreebeardError

# Importer modules are deferred into each subcommand: `treebeard.importers.web`
# pulls in trafilatura + bs4 (~130ms) and would otherwise load on every `tb`
# invocation via command auto-registration. See CLAUDE.md "Startup performance".

DEFAULT_LOOKBACK_DAYS = 7


def _now_utc() -> datetime:
    return datetime.now(UTC)


@click.group("import", cls=RichGroup)
def command() -> None:
    """Import artifacts from external integrations."""


@command.command("granola")
@click.option(
    "--since",
    type=click.DateTime(formats=["%Y-%m-%d"]),
    default=None,
    help="Import notes updated on or after this UTC date "
    f"(default: {DEFAULT_LOOKBACK_DAYS} days ago).",
)
def granola(since: datetime | None) -> None:
    """Import meeting notes from Granola."""
    from treebeard.importers.granola import GranolaImporter
    from treebeard.importers.sync import sync

    cfg = load_config()
    if not cfg.granola_api_key:
        raise TreebeardError(
            "granola_api_key not set",
            hint="add it under [secrets] in config.toml",
        )
    now = _now_utc()
    since_dt = (
        since.replace(tzinfo=UTC)
        if since is not None
        else now - timedelta(days=DEFAULT_LOOKBACK_DAYS)
    )
    importer = GranolaImporter(api_key=cfg.granola_api_key)
    stats = sync(cfg.vault, importer, since=since_dt, now=now)
    click.echo(stats.summary())


@command.command("web")
@click.argument("urls", nargs=-1, required=True, metavar="URL [URL ...]")
def web(urls: tuple[str, ...]) -> None:
    """Import one or more web pages as markdown notes."""
    from treebeard.importers.sync import sync
    from treebeard.importers.web import WebImporter

    cfg = load_config()
    now = _now_utc()
    importer = WebImporter(urls=urls, now=now)
    stats = sync(cfg.vault, importer, since=now, now=now)
    click.echo(stats.summary())
