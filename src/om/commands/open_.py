"""`om open` — interactive picker over the vault.

Lists notes via fzf with a full-file preview pane. Enter opens the
selected note; Ctrl-N creates a new note named after the typed query
(or a fresh scratch if the query is empty). Pass `--limit N` to cap
the list to the N most recently edited notes.
"""

from __future__ import annotations

import pathlib
import subprocess
import time
from datetime import UTC, datetime

import click

from om import fzf, picker, ui
from om.commands.note import create_named_note, create_scratch
from om.config import (
    CONFIG_FILENAME,
    DEFAULT_CONFIG_DIR,
    load_config,
)
from om.editor import reopen
from om.post_edit import PostEditAbort, slugify
from om.ui import OmError
from om.vault import list_recent_notes


def _now_utc() -> datetime:
    return datetime.now(UTC)


def _run_fzf(lines: list[str], previewer: str) -> tuple[str, str, str]:
    """Run fzf with the picker's flags. Returns `(query, key, selection)`.

    `--print-query` puts the query on the first line, `--expect=ctrl-n`
    puts the pressed key on the second line (empty for plain Enter), and
    the selection (if any) is on the third line. Esc/Ctrl-C → exit 130,
    treated as a cancel.
    """
    cmd = [
        *fzf.base_args("om> ", header="enter: open  ctrl-n: new note"),
        "--delimiter=\t",
        "--with-nth=1",
        f"--preview={picker.preview_cmd(previewer)}",
        "--preview-window=right:60%",
        "--expect=ctrl-n",
        "--print-query",
    ]
    proc = subprocess.run(
        cmd,
        input="\n".join(lines),
        text=True,
        capture_output=True,
        check=False,
    )
    if proc.returncode == fzf.CANCELLED_RETURNCODE:
        return ("", "", "")
    out_lines = proc.stdout.split("\n")
    query = out_lines[0] if len(out_lines) > 0 else ""
    key = out_lines[1] if len(out_lines) > 1 else ""
    selection = out_lines[2] if len(out_lines) > 2 else ""
    return (query, key, selection)


def run(vault: pathlib.Path, editor: str, previewer: str, limit: int | None) -> None:
    """Run the picker against `vault`."""
    paths = list_recent_notes(vault, limit)
    if not paths:
        ui.info("vault is empty — create a note with `om note <name>`")
        return

    now_seconds = time.time()
    lines = [picker.format_line(p, now_seconds) for p in paths]
    query, key, selection = _run_fzf(lines, previewer)

    if key == "ctrl-n":
        now = _now_utc()
        query = query.strip()
        if query:
            try:
                slug = slugify(query)
            except PostEditAbort as exc:
                raise OmError(str(exc)) from exc
            target = vault / f"{slug}.md"
            if target.exists():
                raise OmError(
                    f"{target.name} already exists",
                    hint="pick it from the list instead",
                )
            create_named_note(vault, slug, query, now, editor)
        else:
            create_scratch(vault, now, editor)
        return

    if not selection:
        return

    parts = selection.split("\t", 1)
    if len(parts) < 2:
        return
    target_path = pathlib.Path(parts[1])
    if not target_path.exists():
        raise OmError(f"selected file no longer exists: {target_path}")
    reopen(target_path, editor)


@click.command("open")
@click.option(
    "--limit",
    "limit",
    type=int,
    default=None,
    help="Cap the list to the N most recently edited notes (default: no cap).",
)
@click.option(
    "--config-dir",
    "config_dir",
    type=click.Path(),
    default=None,
    help=f"Directory holding {CONFIG_FILENAME} (default: {DEFAULT_CONFIG_DIR}).",
)
@click.pass_context
def command(ctx: click.Context, limit: int | None, config_dir: str | None) -> None:
    """Fuzzy-pick a note across the whole vault and open it."""
    ctx.ensure_object(dict)["config_dir"] = config_dir
    cfg = load_config(config_dir)
    run(cfg.vault, cfg.editor, cfg.previewer, limit)
