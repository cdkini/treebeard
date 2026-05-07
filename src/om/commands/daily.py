"""`om daily` — create or open today's daily note (`YYYY-MM-DD.md`)."""

from __future__ import annotations

from datetime import date, datetime

import click

from om.commands.note import _now_utc, _resolve_editor, create_or_open_named
from om.config import CONFIG_FILENAME, DEFAULT_CONFIG_DIR, load_vault_path

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
def command(config_dir: str | None) -> None:
    """Create or open today's daily note in the vault."""
    vault = load_vault_path(config_dir)
    if vault is None:
        raise click.ClickException("no vault configured; run `om init` first")
    if not vault.is_dir():
        raise click.ClickException(f"configured vault {vault} does not exist")

    editor = _resolve_editor()
    now = _now_utc()
    today = _today_local().isoformat()
    create_or_open_named(vault, today, today, now, editor, tags=[DAILY_TAG])
