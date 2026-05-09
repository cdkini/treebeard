"""Per-tag index note generation.

For each tag carried by at least `THRESHOLD` non-index notes, materialize a
`<vault>/<slug(tag)>.md` whose body is an alphabetical list of wikilinks.
Links use `[[stem|Title]]` so they resolve to the on-disk filename (which
import commands prefix with `<source>-<date>-` for collision avoidance)
while still rendering the human title. The alias is dropped when
`slugify(title) == stem` (daily notes, hand-written notes whose title is
already the filename) so those entries stay readable.

Index notes are marked by `tags: [index]` so they're skipped during the
corpus scan and recognizable on subsequent runs.

Idempotent by design: if the desired body matches what's on disk, the file
is left alone (no `updated_at` bump, no spurious commit). Notes that exist
under a tag's filename but lack the `index` marker are treated as
hand-written and refused with a warning.

When a tag's count falls back below `THRESHOLD` (e.g. after `treebeard archive`),
the now-orphaned index is moved into `.treebeard/archive/` via `treebeard.archiver` so it
stops appearing in `treebeard open` and stops pointing at archived notes via
`[[wikilinks]]`. The move uses the same timestamped-prefix scheme as
`treebeard archive`, so the index is recoverable by a manual `mv` like any other
archived note.

Runs as part of the `_on_close` auto-commit hook on every subcommand, so
indexes stay in sync without a separate `treebeard index` invocation.
"""

from __future__ import annotations

import pathlib
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime

from treebeard import archiver
from treebeard.frontmatter import Frontmatter, Source, split_document, write_note
from treebeard.post_edit import PostEditAbort, slugify

_WIKILINK_UNSAFE_RE = re.compile(r"[\[\]|]")

INDEX_TAG = "index"
THRESHOLD = 3


def _now_utc() -> datetime:
    return datetime.now(UTC)


@dataclass
class IndexStats:
    """Counts and warnings from one `build_indexes` pass.

    `archived` lists the pre-archive paths (vault root) of indexes that
    were moved into `.treebeard/archive/` this pass because their tag dropped
    below `THRESHOLD`.
    """

    wrote: int = 0
    updated: int = 0
    unchanged: int = 0
    skipped: int = 0
    archived: list[pathlib.Path] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def build_indexes(vault: pathlib.Path, *, now: datetime) -> IndexStats:
    """Generate per-tag index notes under `vault`. No I/O outside the vault.

    Returns counts plus any warnings (empty-slug tags, hand-written
    collisions) and the pre-archive paths of indexes auto-archived this
    pass (tag fell below `THRESHOLD`). The `_on_close` hook drops
    `archived` to avoid per-command nag; a future doctor command might
    report it.
    """
    stats = IndexStats()
    corpus, existing_indexes = _scan_vault(vault)
    tag_to_entries = _group_by_tag(corpus)

    eligible_tags: set[str] = set()
    for tag, entries in sorted(tag_to_entries.items()):
        if len(entries) < THRESHOLD:
            continue
        eligible_tags.add(tag)
        result, warning = _upsert_index(vault, tag, entries, now)
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

    stale = [path for path, fm in existing_indexes if fm.title not in eligible_tags]
    if stale:
        archiver.archive_paths(vault, stale, now=now)
        stats.archived = stale
    return stats


def _scan_vault(
    vault: pathlib.Path,
) -> tuple[
    list[tuple[pathlib.Path, Frontmatter]],
    list[tuple[pathlib.Path, Frontmatter]],
]:
    """Single walk of the vault root. Returns (corpus, existing_indexes):
    corpus is parseable non-index notes (used to compute tag eligibility);
    existing_indexes is pre-existing index notes (used to detect stale
    ones whose tag fell below `THRESHOLD`).
    """
    corpus: list[tuple[pathlib.Path, Frontmatter]] = []
    existing_indexes: list[tuple[pathlib.Path, Frontmatter]] = []
    for path in sorted(vault.glob("*.md")):
        parsed = split_document(path.read_text(encoding="utf-8"))
        if parsed is None:
            continue
        fm, _ = parsed
        if fm.tags == [INDEX_TAG]:
            existing_indexes.append((path, fm))
        elif INDEX_TAG not in fm.tags:
            corpus.append((path, fm))
    return corpus, existing_indexes


def _group_by_tag(
    corpus: list[tuple[pathlib.Path, Frontmatter]],
) -> dict[str, list[tuple[str, str]]]:
    """Group `(stem, title)` pairs by each tag they carry. The stem is
    the wikilink target; the title is the display text."""
    grouped: dict[str, list[tuple[str, str]]] = {}
    for path, fm in corpus:
        for tag in fm.tags:
            grouped.setdefault(tag, []).append((path.stem, fm.title))
    return grouped


def _build_body(entries: list[tuple[str, str]]) -> str:
    """Alphabetical (case-insensitive by display title), de-duplicated
    wikilink list, preceded by a blank line so the body breathes after
    the frontmatter.

    Emits `[[stem|title]]` so links resolve to the on-disk filename. When
    `slugify(title) == stem` the alias is redundant (the title already
    renders cleanly as the link text), so we drop it for a tidier index.
    """
    unique = sorted(set(entries), key=lambda entry: entry[1].lower())
    lines: list[str] = []
    for stem, title in unique:
        try:
            redundant = slugify(title) == stem
        except PostEditAbort:
            redundant = False
        if redundant:
            lines.append(f"- [[{stem}]]\n")
        else:
            display = _WIKILINK_UNSAFE_RE.sub(" ", title).strip() or title
            lines.append(f"- [[{stem}|{display}]]\n")
    return "\n" + "".join(lines)


def _upsert_index(
    vault: pathlib.Path,
    tag: str,
    entries: list[tuple[str, str]],
    now: datetime,
) -> tuple[str, str | None]:
    """Create or update `<vault>/<slug(tag)>.md`. Returns (result, warning)
    where result is "wrote" | "updated" | "unchanged" | "skipped"."""
    try:
        slug = slugify(tag)
    except PostEditAbort:
        return "skipped", f"tag {tag!r} produces an empty slug; skipping"
    path = vault / f"{slug}.md"
    desired_body = _build_body(entries)

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
        write_note(path, new_fm, desired_body)
        return "updated", None

    new_fm = Frontmatter.new(tag, now)
    new_fm.tags = [INDEX_TAG]
    write_note(path, new_fm, desired_body)
    return "wrote", None
