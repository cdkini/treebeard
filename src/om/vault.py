"""Filesystem operations over the configured vault directory."""

from __future__ import annotations

import pathlib
import re
from datetime import date

_DATE_FILE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})\.md$")


def find_prior_daily(vault: pathlib.Path, today: date) -> tuple[pathlib.Path, date] | None:
    """Return the most recent daily note strictly before `today`, or None.

    Daily notes are markdown files in `vault` whose basename is a real
    ISO date (`YYYY-MM-DD.md`). Files with a date-shaped but invalid
    name (e.g. `2026-13-99.md`) are ignored.
    """
    best: tuple[pathlib.Path, date] | None = None
    for entry in vault.glob("*.md"):
        match = _DATE_FILE_RE.match(entry.name)
        if match is None:
            continue
        try:
            entry_date = date.fromisoformat(match.group(1))
        except ValueError:
            continue
        if entry_date >= today:
            continue
        if best is None or entry_date > best[1]:
            best = (entry, entry_date)
    return best


def list_recent_notes(vault: pathlib.Path, limit: int | None = 10) -> list[pathlib.Path]:
    """Return markdown files in the vault, mtime desc, optionally capped.

    `.om/` and `.git/` don't contain `.md` files, so a plain rglob is
    enough — no exclude list needed. `limit=None` returns every note.
    """
    entries = [(entry.stat().st_mtime, entry) for entry in vault.rglob("*.md")]
    entries.sort(key=lambda item: item[0], reverse=True)
    paths = [path for _, path in entries]
    return paths if limit is None else paths[:limit]
