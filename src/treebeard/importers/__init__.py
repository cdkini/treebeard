"""Importer interface shared by all `tb import <foo>` integrations.

An `Importer` knows how to fetch notes from one external system. The
`sync()` driver in `treebeard.importers.sync` consumes any `Importer` and lands
its output in the vault idempotently — per-integration code only deals
with the external API, never with vault layout or commit hooks.

To add a new integration:

  1. Implement `Importer` (e.g. `LinearImporter` with `source = "linear"`).
  2. Register a Click subcommand on `command` in `treebeard.commands.import_`
     that constructs the importer and calls `sync()`.

Note shape is normalized to `ImportedNote` so the driver doesn't need to
care which source produced it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol


@dataclass(frozen=True)
class NoteSummary:
    """Lightweight handle returned by the cheap list endpoint.

    `display_title` is shown to the user during progress reporting; the
    full payload (summary markdown, transcript, …) is fetched lazily by
    `Importer.fetch_one`.

    `updated_at` is the source's last-modified timestamp as reported by
    the list endpoint — the driver compares it against the local note's
    `updated_at` to skip `fetch_one` entirely when nothing has changed.
    """

    import_id: str
    display_title: str
    updated_at: datetime


@dataclass(frozen=True)
class ImportedNote:
    """A normalized note ready to land in the vault."""

    import_id: str
    import_url: str
    title: str
    created_at: datetime
    updated_at: datetime
    body_markdown: str
    tags: list[str]


class Importer(Protocol):
    """Pulls notes from one external source.

    `source` is the value written into frontmatter `import_source` and
    used by `sync()` to find existing imported notes from this source
    (so two importers don't fight over the same files).

    `single_shot` is True for importers that always handle exactly one
    user-specified item (e.g. `tb import web <URL>`) and ignore `since`.
    The driver uses it to suppress the "querying since X / found N"
    prelude, which is noise when there's only ever one item and the
    user just typed its identifier on the CLI.

    Two-phase API so the driver can show meaningful progress: a cheap
    `list_summaries` materializes everything we need to show "N of M"
    while iterating, and an expensive `fetch_one` pulls the full body.
    """

    source: str
    single_shot: bool

    def list_summaries(self, *, since: datetime) -> list[NoteSummary]: ...

    def fetch_one(self, summary: NoteSummary) -> ImportedNote: ...
