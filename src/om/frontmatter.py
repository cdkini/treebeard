"""Typed frontmatter for `om` notes.

The `Frontmatter` dataclass parses and serializes the leading `---…---`
block of a markdown note. It owns the schema (title, source, created_at,
updated_at, tags, plus import_source/import_id/import_url for notes
pulled by `om import`) and round-trips any unknown fields verbatim so
we never lose user data on rewrite.
"""

from __future__ import annotations

import pathlib
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum

TIMESTAMP_FMT = "%Y-%m-%dT%H:%M:%SZ"

_BLOCK_RE = re.compile(r"\A---\n(.*?)\n---\n", re.DOTALL)
_KV_RE = re.compile(r"^(?P<key>[A-Za-z_][A-Za-z0-9_]*):[ \t]*(?P<value>.*?)[ \t]*$")
_INLINE_LIST_RE = re.compile(r"^\[(.*)\]$")


class Source(StrEnum):
    USER = "user"
    IMPORT = "import"


@dataclass
class Frontmatter:
    title: str
    source: Source
    created_at: datetime
    updated_at: datetime
    tags: list[str] = field(default_factory=list)
    import_source: str | None = None
    import_id: str | None = None
    import_url: str | None = None
    # Lines we don't recognize, preserved verbatim (no trailing newline).
    extra: list[str] = field(default_factory=list)

    @classmethod
    def new(cls, title: str, now: datetime | None = None) -> Frontmatter:
        ts = now if now is not None else datetime.now(UTC)
        return cls(title=title, source=Source.USER, created_at=ts, updated_at=ts)

    def serialize(self) -> str:
        """Render as a `---…---\\n` block."""
        lines: list[str] = ["---"]
        lines.append(f"title: {self.title}")
        lines.append(f"source: {self.source.value}")
        lines.append(f"created_at: {self.created_at.strftime(TIMESTAMP_FMT)}")
        lines.append(f"updated_at: {self.updated_at.strftime(TIMESTAMP_FMT)}")
        lines.append(f"tags: [{', '.join(self.tags)}]")
        if self.import_source is not None:
            lines.append(f"import_source: {self.import_source}")
        if self.import_id is not None:
            lines.append(f"import_id: {self.import_id}")
        if self.import_url is not None:
            lines.append(f"import_url: {self.import_url}")
        lines.extend(self.extra)
        lines.append("---")
        return "\n".join(lines) + "\n"


def write_note(path: pathlib.Path, fm: Frontmatter, body: str) -> None:
    """Write `fm.serialize() + body` to `path` as UTF-8."""
    path.write_text(fm.serialize() + body, encoding="utf-8")


def split_document(text: str) -> tuple[Frontmatter, str] | None:
    """Parse the leading frontmatter block. Returns (fm, body) or None.

    Returns None when the text does not begin with a `---…---` block, or
    when required scalar fields (title/source/created_at/updated_at) are
    missing or unparseable. Unknown fields are preserved in `fm.extra`.
    """
    match = _BLOCK_RE.match(text)
    if match is None:
        return None
    block = match.group(1)
    body = text[match.end() :]

    title: str | None = None
    source: Source | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    tags: list[str] = []
    import_source: str | None = None
    import_id: str | None = None
    import_url: str | None = None
    extra: list[str] = []

    for raw_line in block.split("\n"):
        kv = _KV_RE.match(raw_line)
        if kv is None:
            extra.append(raw_line)
            continue
        key = kv.group("key")
        value = kv.group("value")
        if key == "title":
            title = value
        elif key == "source":
            try:
                source = Source(value)
            except ValueError:
                extra.append(raw_line)
        elif key == "created_at":
            parsed = _parse_ts(value)
            if parsed is None:
                extra.append(raw_line)
            else:
                created_at = parsed
        elif key == "updated_at":
            parsed = _parse_ts(value)
            if parsed is None:
                extra.append(raw_line)
            else:
                updated_at = parsed
        elif key == "tags":
            inline = _parse_inline_list(value)
            if inline is None:
                # Block-style or otherwise non-inline: preserve verbatim.
                extra.append(raw_line)
            else:
                tags = inline
        elif key == "import_source":
            import_source = value
        elif key == "import_id":
            import_id = value
        elif key == "import_url":
            import_url = value
        else:
            extra.append(raw_line)

    if title is None or source is None or created_at is None or updated_at is None:
        return None

    return (
        Frontmatter(
            title=title,
            source=source,
            created_at=created_at,
            updated_at=updated_at,
            tags=tags,
            import_source=import_source,
            import_id=import_id,
            import_url=import_url,
            extra=extra,
        ),
        body,
    )


def _parse_ts(value: str) -> datetime | None:
    try:
        return datetime.strptime(value, TIMESTAMP_FMT).replace(tzinfo=UTC)
    except ValueError:
        return None


def _parse_inline_list(value: str) -> list[str] | None:
    inner_match = _INLINE_LIST_RE.match(value.strip())
    if inner_match is None:
        return None
    inner = inner_match.group(1).strip()
    if not inner:
        return []
    return [item.strip() for item in inner.split(",") if item.strip()]
