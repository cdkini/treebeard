"""Granola public-API client and importer.

Wraps `https://public-api.granola.ai/v1` and yields `ImportedNote`s that
the shared `sync()` driver can land in the vault.

API shape (from docs.granola.ai/api-reference):
  - GET /notes?updated_after=…&cursor=…&page_size=…
      → { notes: NoteSummary[], hasMore, cursor }
  - GET /notes/{id}?include=transcript
      → { id, title, created_at, updated_at, summary_markdown,
          transcript: [{speaker:{source,…}, text, start_time, end_time}],
          web_url, … }

The list endpoint returns metadata only; we re-fetch each note by id to
get `summary_markdown` and the transcript. That's an N+1 by design, but
the public API rate limit (5 req/s sustained) is well within reach for
typical personal volumes.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any, cast

import httpx

from treebeard.frontmatter import TIMESTAMP_FMT
from treebeard.importers import ImportedNote, NoteSummary
from treebeard.ui import TreebeardError

BASE_URL = "https://public-api.granola.ai/v1"
PAGE_SIZE = 30
DEFAULT_TIMEOUT = 30.0


class GranolaImporter:
    """Implements the `Importer` Protocol for Granola."""

    source = "granola"
    single_shot = False

    def __init__(self, api_key: str, *, client: httpx.Client | None = None) -> None:
        if client is None:
            client = httpx.Client(
                base_url=BASE_URL,
                headers={"Authorization": f"Bearer {api_key}"},
                timeout=DEFAULT_TIMEOUT,
            )
        self._client = client

    def list_summaries(self, *, since: datetime) -> list[NoteSummary]:
        """Page through `/notes` and return lightweight handles."""
        return [
            NoteSummary(
                import_id=cast(str, n["id"]),
                display_title=(cast(str | None, n.get("title")) or "").strip()
                or "Untitled meeting",
                updated_at=_parse_iso8601(cast(str, n["updated_at"])),
            )
            for n in self._list_notes(since)
        ]

    def fetch_one(self, summary: NoteSummary) -> ImportedNote:
        """Hit `/notes/{id}?include=transcript` for the full payload."""
        return _to_imported_note(self._get_note(summary.import_id))

    def _list_notes(self, since: datetime) -> Iterator[dict[str, Any]]:
        cursor: str | None = None
        params: dict[str, str | int] = {
            "updated_after": since.strftime(TIMESTAMP_FMT),
            "page_size": PAGE_SIZE,
        }
        while True:
            page_params = dict(params)
            if cursor is not None:
                page_params["cursor"] = cursor
            resp = self._get("/notes", params=page_params)
            yield from resp.get("notes") or []
            if not resp.get("hasMore"):
                return
            cursor = resp.get("cursor")
            if not cursor:
                # Defensive: API said hasMore but didn't give us a cursor.
                return

    def _get_note(self, note_id: str) -> dict[str, Any]:
        return self._get(f"/notes/{note_id}", params={"include": "transcript"})

    def _get(self, path: str, *, params: dict[str, Any]) -> dict[str, Any]:
        try:
            response = self._client.get(path, params=params)
        except httpx.HTTPError as exc:
            raise TreebeardError(f"granola API request failed: {exc}") from exc
        if response.status_code == 401:
            raise TreebeardError(
                "granola API rejected the api key (401)",
                hint="check granola_api_key under [secrets] in config.toml",
            )
        if response.status_code == 429:
            raise TreebeardError(
                "granola API rate limit hit (429); try again shortly",
            )
        if response.status_code >= 400:
            raise TreebeardError(f"granola API error {response.status_code}: {response.text[:200]}")
        return response.json()


def _to_imported_note(note: dict[str, Any]) -> ImportedNote:
    """Translate a Granola `note` payload into our normalized shape."""
    note_id = note["id"]
    created_at = _parse_iso8601(note["created_at"])
    updated_at = _parse_iso8601(note["updated_at"])
    raw_title = note.get("title")
    title = (raw_title or "").strip() or "Untitled meeting"
    return ImportedNote(
        import_id=note_id,
        import_url=note.get("web_url") or f"https://notes.granola.ai/d/{note_id}",
        title=title,
        created_at=created_at,
        updated_at=updated_at,
        body_markdown=_render_body(note),
        tags=["granola"],
    )


def _render_body(note: dict[str, Any]) -> str:
    """Build the markdown body: AI summary, then transcript."""
    parts: list[str] = ["\n"]
    summary = note.get("summary_markdown")
    if summary:
        parts.append(summary.rstrip() + "\n")
    transcript = note.get("transcript")
    if transcript:
        if summary:
            parts.append("\n")
        parts.append("## Transcript\n\n")
        for turn in transcript:
            label = _speaker_label(turn.get("speaker") or {})
            text = (turn.get("text") or "").strip()
            if not text:
                continue
            parts.append(f"**{label}:** {text}\n\n")
    return "".join(parts)


def _speaker_label(speaker: dict[str, Any]) -> str:
    """Prefer the diarization label; fall back to the audio source."""
    label = speaker.get("diarization_label")
    if isinstance(label, str) and label.strip():
        return label.strip()
    src = speaker.get("source")
    return src if isinstance(src, str) and src else "speaker"


def _parse_iso8601(value: str) -> datetime:
    """Granola returns ISO 8601 with `Z` (or +00:00). Normalize to UTC and
    truncate sub-second precision — frontmatter serialization is
    second-resolution, so keeping microseconds here would make every
    re-run trip the `updated_at` comparison and re-fetch unchanged notes.
    """
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    return datetime.fromisoformat(value).astimezone(UTC).replace(microsecond=0)
