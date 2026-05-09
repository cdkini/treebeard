"""Shared row formatting + preview-command resolution for fzf pickers.

Multiple commands (`tb open`, `tb archive`) present the same vault-note
list to fzf with the same `{title-padded}  {ago}\\t{abspath}` row shape
and the same preview-renderer ladder. Centralizing them here keeps the
two pickers in lockstep — a tweak to title width or preview fallback
order applies to both at once.
"""

from __future__ import annotations

import pathlib

from treebeard import dependencies
from treebeard.frontmatter import split_document
from treebeard.timefmt import humanize_mtime

TITLE_WIDTH = 30

# fzf substitutes `{2}` with the path field (second tab-separated column
# in `_format_line` output). Each value is a shell snippet, not argv.
_PREVIEWER_COMMANDS = {
    "bat": "bat --color=always --style=plain --language=markdown {2}",
    "glow": "glow -s dark {2}",
    "cat": "cat {2}",
}


def preview_cmd(configured: str) -> str:
    """Pick the preview renderer.

    Try the user's configured previewer first, then walk the rest of the
    valid list. `cat` is always present, so the ladder always terminates
    in a usable command — no synthetic floor needed.
    """
    order = [configured, *(name for name in _PREVIEWER_COMMANDS if name != configured)]
    for name in order:
        if dependencies.previewer(name).is_available():
            return _PREVIEWER_COMMANDS[name]
    return _PREVIEWER_COMMANDS["cat"]


def _truncate(text: str, width: int) -> str:
    """Pad/truncate `text` to exactly `width` columns. Long values get an
    ellipsis suffix; short ones are right-padded with spaces so the next
    column starts at a predictable offset."""
    if len(text) <= width:
        return text.ljust(width)
    return text[: width - 1] + "…"


def format_line(path: pathlib.Path, now: float) -> str:
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
