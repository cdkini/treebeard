"""`om grep` — fuzzy content search across the vault.

rg drives the matching: each keystroke re-runs ripgrep through fzf's
`change:reload` binding. fzf is just the UI — `--disabled` so it doesn't
re-filter rg's output, `--ansi` so rg's color survives. Enter opens the
selected match in the editor at the matched line; Esc cancels silently.
"""

from __future__ import annotations

import pathlib
import shlex
import shutil
import subprocess

import click

from om.config import (
    CONFIG_FILENAME,
    DEFAULT_CONFIG_DIR,
    load_config,
)
from om.editor import reopen
from om.ui import OmError

FZF_CANCELLED = 130


def _check_rg() -> None:
    if shutil.which("rg") is None:
        raise OmError("ripgrep is required", hint="install via `brew install ripgrep`")


def _check_fzf() -> None:
    if shutil.which("fzf") is None:
        raise OmError("fzf is required", hint="install via `brew install fzf`")


def _preview_cmd() -> str:
    """Pick the preview renderer. fzf substitutes `{1}` for the path
    field and `{2}` for the line number — bat highlights that line."""
    if shutil.which("bat") is not None:
        return "bat --color=always --style=plain --language=markdown --highlight-line {2} {1}"
    return "cat {1}"


def _rg_reload_cmd(vault: pathlib.Path) -> str:
    """The shell command fzf runs on every keystroke.

    `{q}` is fzf's already-shell-quoted query placeholder — do not wrap
    it again. `|| true` swallows rg's exit-1 (no matches) so fzf doesn't
    flash a transient error; rg exit-2 (real error) is rare and would
    just leave an empty list, same as no-match.
    """
    quoted_vault = shlex.quote(str(vault))
    return (
        "rg --column --line-number --no-heading --color=always "
        f"--smart-case --type md -- {{q}} {quoted_vault} || true"
    )


def _run_fzf(vault: pathlib.Path) -> str:
    """Run fzf with rg wired into change:reload. Returns the selected
    line (`path:line:col:text`) or `""` on cancel/no-selection."""
    cmd = [
        "fzf",
        "--ansi",
        "--disabled",
        "--delimiter=:",
        "--with-nth=1,4..",
        f"--bind=change:reload:{_rg_reload_cmd(vault)}",
        f"--preview={_preview_cmd()}",
        "--preview-window=right:60%:+{2}-/2",
        "--height=80%",
        "--prompt=om-grep> ",
        "--header=enter: open at line  esc: cancel",
    ]
    proc = subprocess.run(
        cmd,
        input="",
        text=True,
        capture_output=True,
        check=False,
    )
    if proc.returncode == FZF_CANCELLED:
        return ""
    return proc.stdout.strip()


def run(vault: pathlib.Path, editor: str) -> None:
    """Run the grep picker against `vault`."""
    _check_rg()
    _check_fzf()

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
        raise OmError(f"selected file no longer exists: {target_path}")

    try:
        line_no: int | None = int(parts[1])
    except ValueError:
        line_no = None

    reopen(target_path, editor, start_line=line_no)


@click.command("grep")
@click.option(
    "--config-dir",
    "config_dir",
    type=click.Path(),
    default=None,
    help=f"Directory holding {CONFIG_FILENAME} (default: {DEFAULT_CONFIG_DIR}).",
)
@click.pass_context
def command(ctx: click.Context, config_dir: str | None) -> None:
    """Fuzzy-search note contents (ripgrep piped through fzf)."""
    ctx.ensure_object(dict)["config_dir"] = config_dir
    cfg = load_config(config_dir)
    run(cfg.vault, cfg.editor)
