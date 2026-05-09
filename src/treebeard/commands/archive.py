"""`tb archive` — soft-delete notes by moving them into `.treebeard/archive/`.

Lists notes via fzf with the same row format as `tb open`. Tab marks
multiple rows; Enter archives the marked set (or the focused row if
nothing is marked). Each archived file is renamed to
`.treebeard/archive/{utc_iso}__{original-filename}`, where the timestamp is
computed once per invocation so a multi-archive groups together
lexicographically.

The archive directory is intentionally inside `.treebeard/`, which is excluded
from `tb open` and `tb grep`'s non-recursive vault glob. Files reappear
nowhere else automatically — restore is a manual `mv` (or a future `tb
restore`). The auto-commit hook at the CLI root records each archive as
a git rename.
"""

from __future__ import annotations

import pathlib
import subprocess
import time

import click

from treebeard import archiver, fzf, picker, ui, vault_layout
from treebeard.config import load_config
from treebeard.vault import list_recent_notes


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


def run(vault: pathlib.Path, previewer: str) -> None:
    """Archive notes selected via fzf. Shared entry for `tb archive`."""
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

    archiver.archive_paths(vault, selected, now=archiver._now_utc())
    for source in selected:
        ui.success(f"archived {source.name}")
    if len(selected) > 1:
        ui.info(f"archived {len(selected)} notes to {vault_layout.ARCHIVE_REL}/")


@click.command("archive")
def command() -> None:
    """Soft-delete notes by moving them into `.treebeard/archive/`."""
    cfg = load_config()
    run(cfg.vault, cfg.previewer)
