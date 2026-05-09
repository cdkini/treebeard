"""Per-tag index note generation.

For each tag carried by at least `THRESHOLD` non-index notes, materialize a
`<vault>/<slug(tag)>.md` whose body is an alphabetical list of `[[Title]]`
links. Index notes are marked by `tags: [index]` so they're skipped during
the corpus scan and recognizable on subsequent runs.

Idempotent by design: if the desired body matches what's on disk, the file
is left alone (no `updated_at` bump, no spurious commit). Notes that exist
under a tag's filename but lack the `index` marker are treated as
hand-written and refused with a warning.

Runs as part of the `_on_close` auto-commit hook on every subcommand, so
indexes stay in sync without a separate `om index` invocation.
"""

from __future__ import annotations

import pathlib
from dataclasses import dataclass, field
from datetime import UTC, datetime

from om.frontmatter import Frontmatter, Source, split_document
from om.post_edit import PostEditAbort, slugify

INDEX_TAG = "index"
THRESHOLD = 3


def _now_utc() -> datetime:
    return datetime.now(UTC)


@dataclass
class IndexStats:
    """Counts and warnings from one `build_indexes` pass."""

    wrote: int = 0
    updated: int = 0
    unchanged: int = 0
    skipped: int = 0
    stale: list[pathlib.Path] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def build_indexes(vault: pathlib.Path, *, now: datetime) -> IndexStats:
    """Generate per-tag index notes under `vault`. No I/O outside the vault.

    Returns counts plus any warnings (empty-slug tags, hand-written
    collisions) and stale index paths. Callers decide whether to surface
    those — the `_on_close` hook drops `stale` to avoid per-command nag,
    while a future doctor command might report it.
    """
    stats = IndexStats()
    corpus = _scan_vault(vault)
    tag_to_titles = _group_by_tag(corpus)

    eligible_tags: set[str] = set()
    for tag, titles in sorted(tag_to_titles.items()):
        if len(titles) < THRESHOLD:
            continue
        eligible_tags.add(tag)
        result, warning = _upsert_index(vault, tag, titles, now)
        if warning is not None:
            stats.warnings.append(warning)
        if result == "wrote":
            stats.wrote += 1
        elif result == "updated":
            stats.updated += 1
        elif result == "unchanged":
            stats.unchanged += 1
        else:
            stats.skipped += 1

    stats.stale = _find_stale(vault, eligible_tags)
    return stats


def _scan_vault(vault: pathlib.Path) -> list[tuple[pathlib.Path, Frontmatter]]:
    """Return (path, frontmatter) for every parseable root note that isn't
    itself an index note."""
    out: list[tuple[pathlib.Path, Frontmatter]] = []
    for path in sorted(vault.glob("*.md")):
        parsed = split_document(path.read_text(encoding="utf-8"))
        if parsed is None:
            continue
        fm, _ = parsed
        if INDEX_TAG in fm.tags:
            continue
        out.append((path, fm))
    return out


def _group_by_tag(
    corpus: list[tuple[pathlib.Path, Frontmatter]],
) -> dict[str, list[str]]:
    """Group note titles by each tag they carry."""
    grouped: dict[str, list[str]] = {}
    for _, fm in corpus:
        for tag in fm.tags:
            grouped.setdefault(tag, []).append(fm.title)
    return grouped


def _build_body(titles: list[str]) -> str:
    """Alphabetical (case-insensitive), de-duplicated wikilink list,
    preceded by a blank line so the body breathes after the frontmatter."""
    unique = sorted(set(titles), key=str.lower)
    return "\n" + "".join(f"- [[{t}]]\n" for t in unique)


def _upsert_index(
    vault: pathlib.Path, tag: str, titles: list[str], now: datetime
) -> tuple[str, str | None]:
    """Create or update `<vault>/<slug(tag)>.md`. Returns (result, warning)
    where result is "wrote" | "updated" | "unchanged" | "skipped"."""
    try:
        slug = slugify(tag)
    except PostEditAbort:
        return "skipped", f"tag {tag!r} produces an empty slug; skipping"
    path = vault / f"{slug}.md"
    desired_body = _build_body(titles)

    if path.exists():
        parsed = split_document(path.read_text(encoding="utf-8"))
        if parsed is None or INDEX_TAG not in parsed[0].tags:
            return "skipped", f"{path.name} exists but is not an index note; skipping"
        old_fm, old_body = parsed
        if old_body == desired_body and old_fm.title == tag and old_fm.tags == [INDEX_TAG]:
            return "unchanged", None
        new_fm = Frontmatter(
            title=tag,
            source=Source.USER,
            created_at=old_fm.created_at,
            updated_at=now,
            tags=[INDEX_TAG],
            extra=old_fm.extra,
        )
        path.write_text(new_fm.serialize() + desired_body, encoding="utf-8")
        return "updated", None

    new_fm = Frontmatter.new(tag, now)
    new_fm.tags = [INDEX_TAG]
    path.write_text(new_fm.serialize() + desired_body, encoding="utf-8")
    return "wrote", None


def _find_stale(vault: pathlib.Path, eligible_tags: set[str]) -> list[pathlib.Path]:
    """Existing index notes whose tag (= title) is no longer eligible."""
    stale: list[pathlib.Path] = []
    for path in sorted(vault.glob("*.md")):
        parsed = split_document(path.read_text(encoding="utf-8"))
        if parsed is None:
            continue
        fm, _ = parsed
        if fm.tags != [INDEX_TAG]:
            continue
        if fm.title not in eligible_tags:
            stale.append(path)
    return stale
