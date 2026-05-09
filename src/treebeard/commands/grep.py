"""`tb grep` — fuzzy content search across the vault.

rg drives the matching: each keystroke re-runs ripgrep through fzf's
`change:reload` binding. fzf is just the UI — `--disabled` so it doesn't
re-filter rg's output, `--ansi` so rg's color survives. Enter opens the
selected match in the editor at the matched line; Esc cancels silently.
"""

from __future__ import annotations

import pathlib
import subprocess

import click

from treebeard import dependencies, fzf
from treebeard.config import load_config
from treebeard.editor import reopen
from treebeard.ui import TreebeardError


def _preview_cmd() -> str:
    """Pick the preview renderer. fzf substitutes `{1}` for the path
    field and `{2}` for the line number — bat highlights that line."""
    if dependencies.BAT.is_available():
        return "bat --color=always --style=plain --language=markdown --highlight-line {2} {1}"
    return "cat {1}"


def _rg_reload_cmd() -> str:
    """The shell command fzf runs on every keystroke.

    `{q}` is fzf's already-shell-quoted query placeholder — do not wrap
    it again. `|| true` swallows rg's exit-1 (no matches) so fzf doesn't
    flash a transient error; rg exit-2 (real error) is rare and would
    just leave an empty list, same as no-match.

    rg searches `.` so paths in results are relative to fzf's cwd (the
    vault), keeping the picker and preview header readable.
    """
    return (
        "rg --column --line-number --no-heading --color=always "
        "--smart-case --type md -- {q} . || true"
    )


def _run_fzf(vault: pathlib.Path) -> str:
    """Run fzf with rg wired into change:reload. Returns the selected
    line (`path:line:col:text`) or `""` on cancel/no-selection."""
    cmd = [
        *fzf.base_args("treebeard-grep> ", header="enter: open at line  esc: cancel"),
        "--ansi",
        "--disabled",
        "--delimiter=:",
        "--with-nth=1,4..",
        f"--bind=change:reload:{_rg_reload_cmd()}",
        f"--preview={_preview_cmd()}",
        "--preview-window=right:60%:+{2}-/2",
    ]
    proc = subprocess.run(
        cmd,
        input="",
        text=True,
        capture_output=True,
        check=False,
        cwd=vault,
    )
    if proc.returncode == fzf.CANCELLED_RETURNCODE:
        return ""
    return proc.stdout.strip()


def run(vault: pathlib.Path, editor: str) -> None:
    """Run the grep picker against `vault`."""

    selection = _run_fzf(vault)
    if not selection:
        return

    parts = selection.split(":", 3)
    if len(parts) < 4:
        return

    target_path = pathlib.Path(parts[0])
    if not target_path.is_absolute():
        target_path = vault / target_path
    if not target_path.exists():
        raise TreebeardError(f"selected file no longer exists: {target_path}")

    try:
        line_no: int | None = int(parts[1])
    except ValueError:
        line_no = None

    reopen(target_path, editor, start_line=line_no)


@click.command("grep")
def command() -> None:
    """Fuzzy-search note contents (ripgrep piped through fzf)."""
    cfg = load_config()
    run(cfg.vault, cfg.editor)
