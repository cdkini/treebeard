"""`om archive` — soft-delete notes by moving them into `.om/archive/`.

Lists notes via fzf with the same row format as `om find`. Tab marks
multiple rows; Enter archives the marked set (or the focused row if
nothing is marked). Each archived file is renamed to
`.om/archive/{utc_iso}__{original-filename}`, where the timestamp is
computed once per invocation so a multi-archive groups together
lexicographically.

The archive directory is intentionally inside `.om/`, which is excluded
from `om find` and `om grep`'s non-recursive vault glob. Files reappear
nowhere else automatically — restore is a manual `mv` (or a future `om
restore`). The auto-commit hook at the CLI root records each archive as
a git rename.
"""

from __future__ import annotations

import pathlib
import subprocess
import time
from datetime import UTC, datetime

import click

from om import fzf, picker, ui
from om.config import (
    CONFIG_FILENAME,
    DEFAULT_CONFIG_DIR,
    load_config,
)
from om.ui import OmError
from om.vault import list_recent_notes

ARCHIVE_DIRNAME = "archive"


def _now_utc() -> datetime:
    return datetime.now(UTC)


def _archive_stamp(now: datetime) -> str:
    """UTC timestamp safe to use as a filename prefix on every filesystem.

    Colons would break on FAT/exFAT/Windows shares, so we use hyphens for
    the time portion. The 'Z' suffix keeps the value unambiguously UTC.
    """
    return now.strftime("%Y-%m-%dT%H-%M-%SZ")


def _run_fzf(lines: list[str], previewer: str) -> list[str]:
    """Run fzf in multi-select mode. Returns the list of selected rows.

    Each row is one of the strings from `lines` (tab-separated
    `display\\tpath`). On cancel (Esc/Ctrl-C → exit 130) returns `[]`.
    Without `--expect` and `--print-query`, fzf's stdout is just the
    selected rows separated by newlines.
    """
    cmd = [
        *fzf.base_args("archive> ", header="tab: mark  enter: archive marked"),
        "--multi",
        "--delimiter=\t",
        "--with-nth=1",
        f"--preview={picker.preview_cmd(previewer)}",
        "--preview-window=right:60%",
    ]
    proc = subprocess.run(
        cmd,
        input="\n".join(lines),
        text=True,
        capture_output=True,
        check=False,
    )
    if proc.returncode == fzf.CANCELLED_RETURNCODE:
        return []
    return [row for row in proc.stdout.split("\n") if row]


def _selected_paths(rows: list[str]) -> list[pathlib.Path]:
    """Extract the path field (column 2) from each fzf row."""
    paths: list[pathlib.Path] = []
    for row in rows:
        parts = row.split("\t", 1)
        if len(parts) < 2:
            continue
        paths.append(pathlib.Path(parts[1]))
    return paths


def _archive_one(source: pathlib.Path, archive_dir: pathlib.Path, stamp: str) -> pathlib.Path:
    """Move `source` into `archive_dir` with a timestamped prefix.

    The timestamp prefix is appended (not checked-and-suffixed) so the
    archive is append-only by construction: re-archiving a same-named
    note after recreating it produces a distinct file every time.
    """
    if not source.exists():
        raise OmError(
            f"selected file no longer exists: {source}",
            hint="re-run `om archive`",
        )
    archive_dir.mkdir(parents=True, exist_ok=True)
    target = archive_dir / f"{stamp}__{source.name}"
    source.rename(target)
    return target


def run(vault: pathlib.Path, previewer: str) -> None:
    """Archive notes selected via fzf. Shared entry for `om archive`."""
    paths = list_recent_notes(vault, None)
    if not paths:
        ui.info("vault is empty — nothing to archive")
        return

    now_seconds = time.time()
    lines = [picker.format_line(p, now_seconds) for p in paths]
    rows = _run_fzf(lines, previewer)
    selected = _selected_paths(rows)
    if not selected:
        return

    archive_dir = vault / ".om" / ARCHIVE_DIRNAME
    stamp = _archive_stamp(_now_utc())
    for source in selected:
        _archive_one(source, archive_dir, stamp)
        ui.success(f"archived {source.name}")
    if len(selected) > 1:
        ui.info(f"archived {len(selected)} notes to .om/{ARCHIVE_DIRNAME}/")


@click.command("archive")
@click.option(
    "--config-dir",
    "config_dir",
    type=click.Path(),
    default=None,
    help=f"Directory holding {CONFIG_FILENAME} (default: {DEFAULT_CONFIG_DIR}).",
)
@click.pass_context
def command(ctx: click.Context, config_dir: str | None) -> None:
    """Soft-delete notes by moving them into `.om/archive/`."""
    ctx.ensure_object(dict)["config_dir"] = config_dir
    cfg = load_config(config_dir)
    run(cfg.vault, cfg.previewer)
