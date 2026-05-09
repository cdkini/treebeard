"""`om import <integration>` — pull notes/artifacts from external integrations.

Group is extensible: add a new integration by registering a subcommand on
`command` (e.g. `@command.command("foo")`). Integrations themselves live
in `om.importers` and implement the `Importer` Protocol; the click
handler is just glue.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import click

from om.config import load_config
from om.importers.granola import GranolaImporter
from om.importers.sync import sync
from om.importers.web import WebImporter
from om.ui import OmError

DEFAULT_LOOKBACK_DAYS = 7


def _now_utc() -> datetime:
    return datetime.now(UTC)


@click.group("import")
@click.pass_context
def command(ctx: click.Context) -> None:
    """Import artifacts from an external integration into the vault."""
    ctx.ensure_object(dict)


@command.command("granola")
@click.option(
    "--since",
    type=click.DateTime(formats=["%Y-%m-%d"]),
    default=None,
    help="Import notes updated on or after this UTC date "
    f"(default: {DEFAULT_LOOKBACK_DAYS} days ago).",
)
@click.pass_context
def granola(ctx: click.Context, since: datetime | None) -> None:
    """Import meeting notes from Granola."""
    ctx.ensure_object(dict)["config_dir"] = None
    cfg = load_config(None)
    if not cfg.granola_api_key:
        raise OmError(
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
@click.argument("url")
@click.pass_context
def web(ctx: click.Context, url: str) -> None:
    """Import a web page as a markdown note."""
    ctx.ensure_object(dict)["config_dir"] = None
    cfg = load_config(None)
    now = _now_utc()
    importer = WebImporter(url=url, now=now)
    stats = sync(cfg.vault, importer, since=now, now=now)
    click.echo(stats.summary())
