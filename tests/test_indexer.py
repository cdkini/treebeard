"""Tests for `om.indexer` — per-tag index note generation.

Exercises `build_indexes` directly. The hook-level integration (does it
actually run on every subcommand, does it land in the same auto-commit as
the user's edit) lives in `tests/test_auto_commit.py`.
"""

from __future__ import annotations

import pathlib
from datetime import UTC, datetime

from om.indexer import build_indexes

NOW = datetime(2026, 5, 7, 14, 23, 5, tzinfo=UTC)


def seed_note(
    vault: pathlib.Path,
    name: str,
    title: str,
    tags: list[str],
    *,
    created_at: str = "2026-05-07T14:23:05Z",
    updated_at: str = "2026-05-07T14:23:05Z",
    body: str = "",
) -> pathlib.Path:
    path = vault / f"{name}.md"
    path.write_text(
        "---\n"
        f"title: {title}\n"
        "source: user\n"
        f"created_at: {created_at}\n"
        f"updated_at: {updated_at}\n"
        f"tags: [{', '.join(tags)}]\n"
        "---\n"
        f"{body}",
        encoding="utf-8",
    )
    return path


def test_below_threshold_no_index(vault: pathlib.Path) -> None:
    seed_note(vault, "a", "Alpha", ["foo"])
    seed_note(vault, "b", "Beta", ["foo"])

    stats = build_indexes(vault, now=NOW)

    assert (stats.wrote, stats.updated, stats.unchanged, stats.skipped) == (0, 0, 0, 0)
    assert stats.stale == []
    assert stats.warnings == []
    assert not (vault / "foo.md").exists()


def test_at_threshold_writes_index(vault: pathlib.Path) -> None:
    seed_note(vault, "a", "Alpha", ["foo"])
    seed_note(vault, "b", "Beta", ["foo"])
    seed_note(vault, "g", "gamma", ["foo"])

    stats = build_indexes(vault, now=NOW)

    assert stats.wrote == 1
    expected = (
        "---\n"
        "title: foo\n"
        "source: user\n"
        "created_at: 2026-05-07T14:23:05Z\n"
        "updated_at: 2026-05-07T14:23:05Z\n"
        "tags: [index]\n"
        "---\n"
        "\n"
        "- [[Alpha]]\n"
        "- [[Beta]]\n"
        "- [[gamma]]\n"
    )
    assert (vault / "foo.md").read_text(encoding="utf-8") == expected


def test_idempotent_second_run(vault: pathlib.Path) -> None:
    seed_note(vault, "a", "Alpha", ["foo"])
    seed_note(vault, "b", "Beta", ["foo"])
    seed_note(vault, "g", "gamma", ["foo"])

    s1 = build_indexes(vault, now=NOW)
    assert s1.wrote == 1
    mtime_after_first = (vault / "foo.md").stat().st_mtime_ns

    s2 = build_indexes(vault, now=NOW)
    assert s2.unchanged == 1
    assert s2.wrote == 0
    assert s2.updated == 0
    # No spurious write means the mtime is preserved.
    assert (vault / "foo.md").stat().st_mtime_ns == mtime_after_first


def test_add_note_updates_index(vault: pathlib.Path) -> None:
    """After adding a 4th note, the index updates: created_at preserved,
    updated_at advanced, body includes the new entry alphabetically."""
    earlier = datetime(2026, 5, 1, 0, 0, 0, tzinfo=UTC)
    later = datetime(2026, 5, 7, 14, 23, 5, tzinfo=UTC)

    seed_note(vault, "a", "Alpha", ["foo"])
    seed_note(vault, "b", "Beta", ["foo"])
    seed_note(vault, "g", "gamma", ["foo"])

    s1 = build_indexes(vault, now=earlier)
    assert s1.wrote == 1

    seed_note(vault, "d", "Delta", ["foo"])
    s2 = build_indexes(vault, now=later)

    assert s2.updated == 1
    text = (vault / "foo.md").read_text(encoding="utf-8")
    assert "created_at: 2026-05-01T00:00:00Z\n" in text
    assert "updated_at: 2026-05-07T14:23:05Z\n" in text
    body_start = text.index("---\n", 4) + len("---\n")
    assert text[body_start:] == "\n- [[Alpha]]\n- [[Beta]]\n- [[Delta]]\n- [[gamma]]\n"


def test_stale_index_reported(vault: pathlib.Path) -> None:
    seed_note(vault, "a", "Alpha", ["foo"])
    seed_note(vault, "b", "Beta", ["foo"])
    seed_note(vault, "g", "gamma", ["foo"])

    s1 = build_indexes(vault, now=NOW)
    assert s1.wrote == 1
    index_before = (vault / "foo.md").read_text(encoding="utf-8")

    # Drop tag from one note → only 2 references remain.
    seed_note(vault, "g", "gamma", [])
    s2 = build_indexes(vault, now=NOW)

    assert [p.name for p in s2.stale] == ["foo.md"]
    # File untouched.
    assert (vault / "foo.md").read_text(encoding="utf-8") == index_before


def test_index_notes_excluded_from_corpus(vault: pathlib.Path) -> None:
    """A note tagged `[foo, index]` must not appear in the foo index, and
    must not contribute to foo's count."""
    seed_note(vault, "a", "Alpha", ["foo"])
    seed_note(vault, "b", "Beta", ["foo"])
    seed_note(vault, "g", "gamma", ["foo"])
    # Pseudo-index note also tagged foo — must be ignored.
    seed_note(vault, "z", "Zeta", ["foo", "index"])

    build_indexes(vault, now=NOW)

    body = (vault / "foo.md").read_text(encoding="utf-8")
    assert "[[Zeta]]" not in body
    assert body.count("- [[") == 3


def test_collision_with_handwritten_note(vault: pathlib.Path) -> None:
    """A pre-existing `foo.md` not tagged `index` must not be clobbered."""
    seed_note(vault, "foo", "foo", ["bar"], body="my hand-written notes\n")
    seed_note(vault, "a", "Alpha", ["foo"])
    seed_note(vault, "b", "Beta", ["foo"])
    seed_note(vault, "g", "gamma", ["foo"])
    original = (vault / "foo.md").read_text(encoding="utf-8")

    stats = build_indexes(vault, now=NOW)

    assert stats.skipped == 1
    assert any("is not an index note" in w for w in stats.warnings)
    assert (vault / "foo.md").read_text(encoding="utf-8") == original


def test_slugified_tag_filename(vault: pathlib.Path) -> None:
    """A tag with mixed case / non-alnum chars produces a slugified filename."""
    seed_note(vault, "a", "Alpha", ["Q2-2026"])
    seed_note(vault, "b", "Beta", ["Q2-2026"])
    seed_note(vault, "g", "gamma", ["Q2-2026"])

    build_indexes(vault, now=NOW)

    assert (vault / "q2-2026.md").exists()
    text = (vault / "q2-2026.md").read_text(encoding="utf-8")
    assert "title: Q2-2026\n" in text


def test_daily_notes_included(vault: pathlib.Path) -> None:
    """Daily-tagged notes count toward the daily index — useful as a
    journal TOC."""
    seed_note(vault, "2026-05-05", "2026-05-05", ["daily"])
    seed_note(vault, "2026-05-06", "2026-05-06", ["daily"])
    seed_note(vault, "2026-05-07", "2026-05-07", ["daily"])

    build_indexes(vault, now=NOW)

    body = (vault / "daily.md").read_text(encoding="utf-8")
    assert "[[2026-05-05]]" in body
    assert "[[2026-05-06]]" in body
    assert "[[2026-05-07]]" in body
