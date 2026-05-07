"""Body templates for note types created by `om`."""

from __future__ import annotations


def compose_daily_body(carryover: list[str]) -> str:
    """Return the body that follows the closing `---\\n` of a daily's
    frontmatter — `### TODOs` and `### Notes` sections, with any
    carry-forward lines injected under TODOs."""
    todos_block = "".join(line + "\n" for line in carryover)
    return f"\n### TODOs\n{todos_block}\n\n### Notes\n\n\n"
