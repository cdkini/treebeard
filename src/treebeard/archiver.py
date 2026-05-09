"""Move notes into `<vault>/.treebeard/archive/` with a timestamped prefix.

Used by `treebeard archive` for user-selected notes and by the auto-indexer for
stale per-tag index notes whose corpus dropped below the index threshold.
The timestamp prefix is appended (not checked-and-suffixed) so the archive
is append-only by construction: re-archiving a same-named note after
recreating it produces a distinct file every time.
"""

from __future__ import annotations

import pathlib
from datetime import UTC, datetime

from treebeard import vault_layout
from treebeard.ui import TreebeardError


def _now_utc() -> datetime:
    return datetime.now(UTC)


def archive_stamp(now: datetime) -> str:
    """UTC timestamp safe to use as a filename prefix on every filesystem.

    Colons would break on FAT/exFAT/Windows shares, so we use hyphens for
    the time portion. The 'Z' suffix keeps the value unambiguously UTC.
    """
    return now.strftime("%Y-%m-%dT%H-%M-%SZ")


def archive_dir(vault: pathlib.Path) -> pathlib.Path:
    return vault_layout.archive_dir(vault)


def archive_paths(
    vault: pathlib.Path, paths: list[pathlib.Path], *, now: datetime
) -> list[pathlib.Path]:
    """Move each `paths` entry into `<vault>/.treebeard/archive/` with a shared
    `archive_stamp(now)` prefix. Returns the new locations in the same
    order. Missing sources raise `TreebeardError`."""
    if not paths:
        return []
    target_dir = archive_dir(vault)
    stamp = archive_stamp(now)
    target_dir.mkdir(parents=True, exist_ok=True)
    out: list[pathlib.Path] = []
    for source in paths:
        if not source.exists():
            raise TreebeardError(
                f"selected file no longer exists: {source}",
                hint="re-run `treebeard archive`",
            )
        target = target_dir / f"{stamp}__{source.name}"
        source.rename(target)
        out.append(target)
    return out
