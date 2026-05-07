"""`om daily` — create or open today's daily note (`YYYY-MM-DD.md`)."""

from __future__ import annotations

from datetime import date, datetime

import click

from om.commands import note as note_cmd
from om.config import CONFIG_FILENAME, DEFAULT_CONFIG_DIR, load_config
from om.editor import edit_with_initial, reopen
from om.frontmatter import Frontmatter
from om.scaffold import compose_daily_body
from om.todos import extract_carryover
from om.vault import find_prior_daily

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
    today = _today_local()
    path = cfg.vault / f"{today.isoformat()}.md"
    if path.exists():
        reopen(path, cfg.editor)
        return

    fm = Frontmatter.new(today.isoformat(), note_cmd._now_utc())
    fm.tags = [DAILY_TAG]
    prior = find_prior_daily(cfg.vault, today)
    carryover: list[str] = []
    if prior is not None:
        prior_path, prior_date = prior
        carryover = extract_carryover(prior_path.read_text(encoding="utf-8"), prior_date)
    initial = fm.serialize() + compose_daily_body(carryover)
    edit_with_initial(path, initial, cfg.editor, keep_when_unchanged=bool(carryover))
