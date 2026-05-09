"""Carry-forward extraction of open TODOs from a prior daily note.

Pure functions over text. No filesystem, no Click — this module is
trivially testable in isolation.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

from treebeard.frontmatter import split_document

_BULLET_RE = re.compile(r"^(?P<indent>\s*)- (?:\[(?P<box>[ xX])\]\s+)?(?P<rest>\S.*)$")
_FROM_SUFFIX_RE = re.compile(r" \(from \d{2}/\d{2}\)$")


@dataclass
class _Frame:
    indent: int
    completed: bool
    emitted_open: bool


def extract_carryover(text: str, source_date: date) -> list[str]:
    """Return the lines to carry forward from `text` (a prior daily note).

    Rules:
      - `- [x]` task gates its whole subtree (nothing under it carries).
      - `- [ ]` task carries with its non-task children and any
        non-completed descendant tasks.
      - Plain bullets (`- foo`) ride along only when they sit under an
        emitted open ancestor with no completed ancestor between.
      - Blank line ends the current bullet group.
      - Top-level open parents get a ` (from MM/DD)` suffix; nested
        open children inside a carried subtree do not. A line that
        already ends in `(from MM/DD)` keeps its existing suffix.
    """
    parsed = split_document(text)
    body = parsed[1] if parsed is not None else text
    suffix = f" (from {source_date.strftime('%m/%d')})"

    out: list[str] = []
    stack: list[_Frame] = []

    for line in body.split("\n"):
        if line.strip() == "":
            stack.clear()
            continue

        match = _BULLET_RE.match(line)
        if match is None:
            stack.clear()
            continue

        indent = len(match.group("indent"))
        while stack and stack[-1].indent >= indent:
            stack.pop()

        box = match.group("box")
        has_completed_ancestor = any(f.completed for f in stack)
        has_emitted_open_ancestor = any(f.emitted_open for f in stack)

        if box in ("x", "X"):
            stack.append(_Frame(indent=indent, completed=True, emitted_open=False))
            continue

        if box == " ":
            if has_completed_ancestor:
                stack.append(_Frame(indent=indent, completed=False, emitted_open=False))
                continue
            if has_emitted_open_ancestor or _FROM_SUFFIX_RE.search(line):
                out.append(line)
            else:
                out.append(line + suffix)
            stack.append(_Frame(indent=indent, completed=False, emitted_open=True))
            continue

        # Plain bullet (no checkbox).
        if has_emitted_open_ancestor and not has_completed_ancestor:
            out.append(line)
        # Don't push plain bullets — they don't anchor a subtree.

    seen: set[str] = set()
    deduped: list[str] = []
    for line in out:
        if line in seen:
            continue
        seen.add(line)
        deduped.append(line)
    return deduped
