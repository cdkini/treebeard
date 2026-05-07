"""`om find` — interactive picker over the vault.

Lists notes via fzf with a full-file preview pane. Enter opens the
selected note; Ctrl-N creates a new note named after the typed query
(or a fresh scratch if the query is empty).

Bare `om` dispatches to this command with `limit=10` (recent-only).
Explicit `om find` shows every note by default; pass `--limit N` to cap.
"""

from __future__ import annotations

import pathlib
import shutil
import subprocess
import time
from datetime import UTC, datetime

import click

from om import ui
from om.commands.note import create_named_note, create_scratch
from om.config import (
    CONFIG_FILENAME,
    DEFAULT_CONFIG_DIR,
    load_config,
)
from om.editor import reopen
from om.frontmatter import split_document
from om.post_edit import PostEditAbort, slugify
from om.timefmt import humanize_mtime
from om.ui import OmError
from om.vault import list_recent_notes

BARE_LIMIT = 20
FZF_CANCELLED = 130
TITLE_WIDTH = 30

# fzf substitutes `{2}` with the path field for `om find`. Each command
# is a shell snippet, not an argv list.
_PREVIEWER_COMMANDS = {
    "bat": "bat --color=always --style=plain --language=markdown {2}",
    "glow": "glow -s dark {2}",
    "cat": "cat {2}",
}


def _now_utc() -> datetime:
    return datetime.now(UTC)


def _check_fzf() -> None:
    if shutil.which("fzf") is None:
        raise OmError("fzf is required", hint="install via `brew install fzf`")


def _preview_cmd(configured: str) -> str:
    """Pick the preview renderer.

    Try the user's configured previewer first, then walk the rest of the
    valid list. `cat` is always present, so the ladder always terminates
    in a usable command — no synthetic floor needed.
    """
    order = [configured, *(name for name in _PREVIEWER_COMMANDS if name != configured)]
    for name in order:
        if shutil.which(name) is not None:
            return _PREVIEWER_COMMANDS[name]
    return _PREVIEWER_COMMANDS["cat"]


def _truncate(text: str, width: int) -> str:
    """Pad/truncate `text` to exactly `width` columns. Long values get an
    ellipsis suffix; short ones are right-padded with spaces so the next
    column starts at a predictable offset."""
    if len(text) <= width:
        return text.ljust(width)
    return text[: width - 1] + "…"


def _format_line(path: pathlib.Path, now: float) -> str:
    """`{title-padded}  {ago}\\t{abspath}`.

    The first field is what fzf shows and matches; the second (path) is
    what `{2}` resolves to in `--preview`. After title-canonical renames
    the filename stem always equals `slugify(title)`, so we don't show
    the stem separately. Title is truncated to `TITLE_WIDTH` so the ago
    column stays aligned across rows.
    """
    try:
        contents = path.read_text(encoding="utf-8")
    except OSError:
        contents = ""
    parsed = split_document(contents)
    title = parsed[0].title.strip() if parsed is not None else ""
    if not title:
        title = path.stem
    try:
        ago = humanize_mtime(now - path.stat().st_mtime)
    except OSError:
        ago = ""
    display = f"{_truncate(title, TITLE_WIDTH)}  {ago}"
    return f"{display}\t{path}"


def _run_fzf(lines: list[str], previewer: str) -> tuple[str, str, str]:
    """Run fzf with the picker's flags. Returns `(query, key, selection)`.

    `--print-query` puts the query on the first line, `--expect=ctrl-n`
    puts the pressed key on the second line (empty for plain Enter), and
    the selection (if any) is on the third line. Esc/Ctrl-C → exit 130,
    treated as a cancel.
    """
    cmd = [
        "fzf",
        "--delimiter=\t",
        "--with-nth=1",
        f"--preview={_preview_cmd(previewer)}",
        "--preview-window=right:60%",
        "--height=80%",
        "--prompt=om> ",
        "--header=enter: open  ctrl-n: new note",
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
    if proc.returncode == FZF_CANCELLED:
        return ("", "", "")
    out_lines = proc.stdout.split("\n")
    query = out_lines[0] if len(out_lines) > 0 else ""
    key = out_lines[1] if len(out_lines) > 1 else ""
    selection = out_lines[2] if len(out_lines) > 2 else ""
    return (query, key, selection)


def run(vault: pathlib.Path, editor: str, previewer: str, limit: int | None) -> None:
    """Run the picker against `vault`. Shared by `om find` and bare `om`."""
    _check_fzf()
    paths = list_recent_notes(vault, limit)
    if not paths:
        ui.info("vault is empty — create a note with `om note <name>`")
        return

    now_seconds = time.time()
    lines = [_format_line(p, now_seconds) for p in paths]
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


@click.command("find")
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
    """Fuzzy-find a note across the whole vault and open it."""
    ctx.ensure_object(dict)["config_dir"] = config_dir
    cfg = load_config(config_dir)
    run(cfg.vault, cfg.editor, cfg.previewer, limit)
