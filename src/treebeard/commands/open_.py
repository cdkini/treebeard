"""`tb open` — picker (or non-interactive top-match) over the vault.

With no QUERY, lists notes via fzf with a full-file preview pane. Enter
opens the selected note; Ctrl-N creates a new note named after the typed
query (or a fresh scratch if the query is empty). With QUERY, fuzzy-matches
against vault filename stems via `fzf --filter` and opens the top match,
erroring if nothing matches. Pass `--limit N` to cap the candidate pool
to the N most recently edited notes in either mode.
"""

from __future__ import annotations

import pathlib
import subprocess
import time

import click

from treebeard import fzf, picker, ui
from treebeard.commands.note import create_named_note, create_scratch
from treebeard.config import load_config
from treebeard.editor import reopen
from treebeard.post_edit import PostEditAbort, slugify
from treebeard.timefmt import now_utc
from treebeard.ui import TreebeardError
from treebeard.vault import list_recent_notes


def _run_fzf(lines: list[str], previewer: str) -> tuple[str, str, str]:
    """Run fzf with the picker's flags. Returns `(query, key, selection)`.

    `--print-query` puts the query on the first line, `--expect=ctrl-n`
    puts the pressed key on the second line (empty for plain Enter), and
    the selection (if any) is on the third line. Esc/Ctrl-C → exit 130,
    treated as a cancel.
    """
    cmd = [
        *fzf.base_args("treebeard> ", header="enter: open  ctrl-n: new note"),
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


def _run_fzf_filter(query: str, lines: list[str]) -> str | None:
    """Run `fzf --filter` non-interactively. Returns the top-scoring line or
    None if nothing matched. Raises TreebeardError on subprocess error.

    fzf prints matches best-first, exits 1 when nothing matches, and exits
    >1 on a real error (bad flag, etc.) — we surface those so a future fzf
    quirk can't masquerade as a no-match.
    """
    cmd = ["fzf", f"--filter={query}"]
    proc = subprocess.run(
        cmd,
        input="\n".join(lines),
        text=True,
        capture_output=True,
        check=False,
    )
    if proc.returncode == 1 or not proc.stdout.strip():
        return None
    if proc.returncode != 0:
        raise TreebeardError(f"fzf failed: {proc.stderr.strip() or proc.returncode}")
    return proc.stdout.split("\n", 1)[0]


def run_interactive(vault: pathlib.Path, editor: str, previewer: str, limit: int | None) -> None:
    """Run the picker against `vault`."""
    paths = list_recent_notes(vault, limit)
    if not paths:
        ui.info("vault is empty — create a note with `tb note <name>`")
        return

    now_seconds = time.time()
    lines = [picker.format_line(p, now_seconds) for p in paths]
    query, key, selection = _run_fzf(lines, previewer)

    if key == "ctrl-n":
        now = now_utc()
        query = query.strip()
        if query:
            try:
                slug = slugify(query)
            except PostEditAbort as exc:
                raise TreebeardError(str(exc)) from exc
            target = vault / f"{slug}.md"
            if target.exists():
                raise TreebeardError(
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
        raise TreebeardError(f"selected file no longer exists: {target_path}")
    reopen(target_path, editor)


def run_query(vault: pathlib.Path, editor: str, query: str, limit: int | None) -> None:
    """Open the top fuzzy-match for `query` against vault filename stems."""
    paths = list_recent_notes(vault, limit)
    if not paths:
        raise TreebeardError("vault is empty", hint="create a note with `tb note <name>`")
    # Flat vault → stem is unique. Match against stems only so the query
    # isn't fighting directory prefixes or title text.
    by_stem = {p.stem: p for p in paths}
    top_stem = _run_fzf_filter(query, list(by_stem.keys()))
    if top_stem is None:
        raise TreebeardError(
            f"no note matches {query!r}",
            hint="try `tb open` for the interactive picker",
        )
    target_path = by_stem[top_stem]
    if not target_path.exists():
        raise TreebeardError(f"matched file no longer exists: {target_path}")
    reopen(target_path, editor)


@click.command("open")
@click.argument("query", nargs=-1)
@click.option(
    "--limit",
    "limit",
    type=int,
    default=None,
    help="Cap the list to the N most recently edited notes (default: no cap).",
)
def command(query: tuple[str, ...], limit: int | None) -> None:
    """Fuzzy-pick a note and open it.

    With no QUERY, opens an interactive picker. With QUERY, opens the top
    fuzzy match against vault filename stems (errors if none match).
    """
    cfg = load_config()
    if query:
        run_query(cfg.vault, cfg.editor, " ".join(query), limit)
    else:
        run_interactive(cfg.vault, cfg.editor, cfg.previewer, limit)
