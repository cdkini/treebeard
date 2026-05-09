"""Vault sync driver — reusable across all importers.

Walks the vault once to find existing imported notes for `importer.source`,
then for each `ImportedNote` decides write / overwrite / no-op based on
`updated_at` comparison. Pure function of (importer output, vault state) —
no state file, no hash. Idempotency primitives live in each note's own
frontmatter (`import_source`, `import_id`, `updated_at`).

The `_on_close` hook in `treebeard.cli` auto-commits whatever this writes; we
deliberately don't touch git here.
"""

from __future__ import annotations

import pathlib
from dataclasses import dataclass
from datetime import datetime

from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    TextColumn,
    TimeElapsedColumn,
)

from treebeard import ui
from treebeard.frontmatter import Frontmatter, Source, split_document, write_note
from treebeard.importers import ImportedNote, Importer
from treebeard.post_edit import PostEditAbort, slugify


@dataclass
class SyncStats:
    wrote: int = 0
    updated: int = 0
    unchanged: int = 0
    skipped: int = 0

    def summary(self) -> str:
        return (
            f"wrote {self.wrote}, updated {self.updated}, "
            f"unchanged {self.unchanged}, skipped {self.skipped}"
        )


def sync(
    vault: pathlib.Path,
    importer: Importer,
    *,
    since: datetime,
    now: datetime,
) -> SyncStats:
    """Pull notes from `importer` into `vault`. Returns counts for
    user-facing summary.

    Per-note decision (by `import_id`):
      - missing locally → write new
      - remote.updated_at > local.updated_at → overwrite (preserve created_at)
      - otherwise → no-op (counts as unchanged)

    Slug collisions (a different note already owns the desired filename)
    are logged and skipped — same posture as `tb index` for hand-written
    files.
    """
    existing = _index_existing(vault, importer.source)
    stats = SyncStats()

    if not importer.single_shot:
        ui.info(f"querying {importer.source} for notes updated since {since:%Y-%m-%d %H:%M}…")
    summaries = importer.list_summaries(since=since)
    if not summaries:
        ui.info("no notes returned")
        return stats
    if not importer.single_shot:
        ui.info(f"found {len(summaries)} note{'s' if len(summaries) != 1 else ''}")

    with Progress(
        TextColumn("[dim]{task.description}[/dim]"),
        BarColumn(),
        MofNCompleteColumn(),
        TimeElapsedColumn(),
        console=ui.status_console,
        transient=True,
    ) as progress:
        task = progress.add_task("fetching", total=len(summaries))
        for summary in summaries:
            progress.update(task, description=_truncate(summary.display_title, 40))
            prior = existing.get(summary.import_id)
            # Short-circuit: when the list-endpoint updated_at matches
            # what we already have, the body can't have changed — skip
            # the (expensive) per-note GET entirely.
            if prior is not None and summary.updated_at <= prior[1].updated_at:
                stats.unchanged += 1
                progress.advance(task)
                continue
            note = importer.fetch_one(summary)
            result = _apply(vault, note, importer.source, existing, now)
            if result == "wrote":
                stats.wrote += 1
            elif result == "updated":
                stats.updated += 1
            elif result == "unchanged":
                stats.unchanged += 1
            else:
                stats.skipped += 1
            progress.advance(task)

    return stats


def _truncate(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    return value[: limit - 1] + "…"


def _index_existing(
    vault: pathlib.Path, source: str
) -> dict[str, tuple[pathlib.Path, Frontmatter]]:
    """Map `import_id` → (path, frontmatter) for notes from this source."""
    out: dict[str, tuple[pathlib.Path, Frontmatter]] = {}
    for path in sorted(vault.glob("*.md")):
        parsed = split_document(path.read_text(encoding="utf-8"))
        if parsed is None:
            continue
        fm, _ = parsed
        if fm.import_source != source or fm.import_id is None:
            continue
        out[fm.import_id] = (path, fm)
    return out


def _apply(
    vault: pathlib.Path,
    note: ImportedNote,
    source: str,
    existing: dict[str, tuple[pathlib.Path, Frontmatter]],
    now: datetime,
) -> str:
    """Returns one of "wrote" | "updated" | "unchanged" | "skipped"."""
    del now  # Kept in the signature for symmetry with treebeard.index — we
    # currently use the remote `updated_at` rather than wall-clock.
    prior = existing.get(note.import_id)

    if prior is not None:
        path, fm = prior
        if note.updated_at > fm.updated_at:
            new_fm = Frontmatter(
                title=note.title,
                source=Source.IMPORT,
                created_at=fm.created_at,
                updated_at=note.updated_at,
                tags=note.tags,
                import_source=source,
                import_id=note.import_id,
                import_url=note.import_url,
                extra=fm.extra,
            )
            write_note(path, new_fm, note.body_markdown)
            return "updated"
        return "unchanged"

    try:
        title_slug = slugify(note.title)
    except PostEditAbort:
        ui.warn(f"title {note.title!r} produces an empty slug; skipping")
        return "skipped"

    date = note.created_at.strftime("%Y-%m-%d")
    path = vault / f"{source}-{date}-{title_slug}.md"
    if path.exists():
        ui.warn(f"{path.name} already exists (different import_id); skipping")
        return "skipped"

    fm = Frontmatter(
        title=note.title,
        source=Source.IMPORT,
        created_at=note.created_at,
        updated_at=note.updated_at,
        tags=note.tags,
        import_source=source,
        import_id=note.import_id,
        import_url=note.import_url,
    )
    write_note(path, fm, note.body_markdown)
    return "wrote"
