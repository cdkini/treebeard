"""`om daily` — create or open today's daily note (`YYYY-MM-DD.md`)."""

from __future__ import annotations

from datetime import date, datetime

import click

from om.commands.note import _now_utc, create_or_open_named
from om.config import CONFIG_FILENAME, DEFAULT_CONFIG_DIR, load_config

DAILY_TAG = "daily"


def _today_local() -> date:
    return datetime.now().astimezone().date()


@click.command("daily")
@click.option(
    "--config-dir",
    "config_dir",
    type=click.Path(),
    default=None,
    help=f"Directory holding {CONFIG_FILENAME} (default: {DEFAULT_CONFIG_DIR}).",
)
@click.pass_context
def command(ctx: click.Context, config_dir: str | None) -> None:
    """Create or open today's daily note in the vault."""
    ctx.ensure_object(dict)["config_dir"] = config_dir
    cfg = load_config(config_dir)
    now = _now_utc()
    today = _today_local().isoformat()
    create_or_open_named(cfg.vault, today, today, now, cfg.editor, tags=[DAILY_TAG])
