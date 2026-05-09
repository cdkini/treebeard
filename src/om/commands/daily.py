"""`om daily` — create or open today's daily note (`YYYY-MM-DD.md`)."""

from __future__ import annotations

from datetime import date, datetime

import click

from om.commands.note import create_named_note
from om.config import load_config
from om.editor import reopen
from om.scaffold import compose_daily_body
from om.timefmt import now_utc
from om.todos import extract_carryover
from om.vault import find_prior_daily

DAILY_TAG = "daily"


def _today_local() -> date:
    return datetime.now().astimezone().date()


@click.command("daily")
def command() -> None:
    """Create or open today's daily note in the vault."""
    cfg = load_config()
    today = _today_local()
    path = cfg.vault / f"{today.isoformat()}.md"
    if path.exists():
        reopen(path, cfg.editor)
        return

    prior = find_prior_daily(cfg.vault, today)
    carryover: list[str] = []
    if prior is not None:
        prior_path, prior_date = prior
        carryover = extract_carryover(prior_path.read_text(encoding="utf-8"), prior_date)
    create_named_note(
        cfg.vault,
        today.isoformat(),
        today.isoformat(),
        now_utc(),
        cfg.editor,
        tags=[DAILY_TAG],
        body=compose_daily_body(carryover),
        keep_when_unchanged=bool(carryover),
    )
