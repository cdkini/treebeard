"""Tests for `om.todos.extract_carryover` — pure function over text."""

from __future__ import annotations

from datetime import date

from om.todos import extract_carryover

PRIOR = date(2026, 5, 6)


def test_empty_input() -> None:
    assert extract_carryover("", PRIOR) == []


def test_flat_open_todos_get_from_suffix() -> None:
    text = "- [ ] write the design doc\n- [x] reply to Carl\n- [ ] schedule retro\n"
    assert extract_carryover(text, PRIOR) == [
        "- [ ] write the design doc (from 05/06)",
        "- [ ] schedule retro (from 05/06)",
    ]


def test_completed_parent_drops_entire_subtree() -> None:
    text = "- [x] Send weekly update\n    - [ ] follow up with Alice\n    - drafted Monday\n"
    assert extract_carryover(text, PRIOR) == []


def test_open_parent_carries_subtree_minus_completed_children() -> None:
    text = (
        "- [ ] Ship migration\n"
        "    - needs SRE signoff\n"
        "    - [x] write rollback script\n"
        "    - [ ] update runbook\n"
    )
    assert extract_carryover(text, PRIOR) == [
        "- [ ] Ship migration (from 05/06)",
        "    - needs SRE signoff",
        "    - [ ] update runbook",
    ]


def test_existing_from_suffix_is_preserved() -> None:
    text = "- [ ] foo (from 05/04)\n"
    assert extract_carryover(text, PRIOR) == ["- [ ] foo (from 05/04)"]


def test_nested_open_child_does_not_get_double_suffix() -> None:
    text = "- [ ] parent\n    - [ ] child\n"
    assert extract_carryover(text, PRIOR) == [
        "- [ ] parent (from 05/06)",
        "    - [ ] child",
    ]


def test_blank_line_ends_bullet_group() -> None:
    text = "- [x] done\n\n- foo\n"
    # Plain bullet after blank line has no open-ancestor → not carried.
    assert extract_carryover(text, PRIOR) == []


def test_dedup_identical_lines() -> None:
    text = "- [ ] dupe\n- [ ] dupe\n"
    assert extract_carryover(text, PRIOR) == ["- [ ] dupe (from 05/06)"]


def test_strips_frontmatter_before_extracting() -> None:
    text = (
        "---\n"
        "title: 2026-05-06\n"
        "source: user\n"
        "created_at: 2026-05-06T10:00:00Z\n"
        "updated_at: 2026-05-06T10:00:00Z\n"
        "tags: [daily]\n"
        "---\n"
        "- [ ] keeper\n"
    )
    assert extract_carryover(text, PRIOR) == ["- [ ] keeper (from 05/06)"]


def test_malformed_frontmatter_treats_whole_input_as_body() -> None:
    text = "no frontmatter here\n- [ ] keeper\n"
    assert extract_carryover(text, PRIOR) == ["- [ ] keeper (from 05/06)"]
