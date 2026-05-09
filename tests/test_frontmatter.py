"""Tests for `om.frontmatter`."""

from __future__ import annotations

from datetime import UTC, datetime

from om.frontmatter import Frontmatter, Source, split_document

NOW = datetime(2026, 5, 7, 14, 23, 5, tzinfo=UTC)


def test_new_uses_provided_now_for_both_timestamps() -> None:
    fm = Frontmatter.new("hello", NOW)
    assert fm.title == "hello"
    assert fm.source is Source.USER
    assert fm.created_at == NOW
    assert fm.updated_at == NOW
    assert fm.tags == []


def test_serialize_round_trip() -> None:
    fm = Frontmatter.new("hello", NOW)
    fm.tags = ["a", "b"]
    text = fm.serialize() + "body\n"
    parsed = split_document(text)
    assert parsed is not None
    out, body = parsed
    assert out == fm
    assert body == "body\n"


def test_split_returns_none_when_no_leading_block() -> None:
    assert split_document("just a body\n") is None


def test_split_returns_none_when_required_field_missing() -> None:
    text = (
        "---\n"
        "title: x\n"
        "source: user\n"
        "created_at: 2026-05-07T14:23:05Z\n"
        # updated_at missing
        "tags: []\n"
        "---\n"
    )
    assert split_document(text) is None


def test_unknown_lines_preserved_in_extra() -> None:
    text = (
        "---\n"
        "title: x\n"
        "source: user\n"
        "created_at: 2026-05-07T14:23:05Z\n"
        "updated_at: 2026-05-07T14:23:05Z\n"
        "tags: []\n"
        "extra_key: keep me\n"
        "---\n"
    )
    parsed = split_document(text)
    assert parsed is not None
    fm, _ = parsed
    assert "extra_key: keep me" in fm.extra
    # Round-trip preserves the unknown line.
    assert "extra_key: keep me\n" in fm.serialize()


def test_block_style_tags_fall_through_to_extra() -> None:
    text = (
        "---\n"
        "title: x\n"
        "source: user\n"
        "created_at: 2026-05-07T14:23:05Z\n"
        "updated_at: 2026-05-07T14:23:05Z\n"
        "tags:\n"
        "  - a\n"
        "  - b\n"
        "---\n"
    )
    parsed = split_document(text)
    assert parsed is not None
    fm, _ = parsed
    assert fm.tags == []
    assert "tags:" in fm.extra
    assert "  - a" in fm.extra


def test_inline_tags_round_trip() -> None:
    text = (
        "---\n"
        "title: x\n"
        "source: user\n"
        "created_at: 2026-05-07T14:23:05Z\n"
        "updated_at: 2026-05-07T14:23:05Z\n"
        "tags: [foo, bar]\n"
        "---\n"
    )
    parsed = split_document(text)
    assert parsed is not None
    fm, _ = parsed
    assert fm.tags == ["foo", "bar"]
    assert "tags: [foo, bar]\n" in fm.serialize()


def test_user_note_omits_import_fields() -> None:
    """User-authored notes don't gain empty `import_*` lines on re-serialize."""
    fm = Frontmatter.new("hello", NOW)
    text = fm.serialize()
    assert "import_source" not in text
    assert "import_id" not in text
    assert "import_url" not in text


def test_import_note_round_trip() -> None:
    text = (
        "---\n"
        "title: 1:1 with Sarah\n"
        "source: import\n"
        "created_at: 2026-05-07T14:23:05Z\n"
        "updated_at: 2026-05-07T15:00:00Z\n"
        "tags: [granola]\n"
        "import_source: granola\n"
        "import_id: not_abc12345678901\n"
        "import_url: https://notes.granola.ai/d/not_abc12345678901\n"
        "---\n"
        "body\n"
    )
    parsed = split_document(text)
    assert parsed is not None
    fm, body = parsed
    assert fm.source is Source.IMPORT
    assert fm.import_source == "granola"
    assert fm.import_id == "not_abc12345678901"
    assert fm.import_url == "https://notes.granola.ai/d/not_abc12345678901"
    assert body == "body\n"
    # Round-trip preserves every typed field.
    assert fm.serialize() + body == text
