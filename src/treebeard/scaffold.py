"""Body templates for note types created by `treebeard`."""

from __future__ import annotations

from importlib import resources


def compose_daily_body(carryover: list[str]) -> str:
    """Return the body that follows the closing `---\\n` of a daily's
    frontmatter — `### TODOs` and `### Notes` sections, with any
    carry-forward lines injected under TODOs."""
    todos_block = "".join(line + "\n" for line in carryover)
    return f"\n### TODOs\n{todos_block}\n\n### Notes\n\n\n"


def compose_claude_md() -> str:
    """Return the scaffolded `.claude/CLAUDE.md` template body.

    Loaded from `treebeard/prompts/CLAUDE.md` via `importlib.resources` so
    edits to the markdown don't require touching Python code, and the
    file is included in wheel builds (hatch picks up package data
    under `src/treebeard/` by default). Static — same bytes every time."""
    return resources.files("treebeard").joinpath("prompts/CLAUDE.md").read_text(encoding="utf-8")
