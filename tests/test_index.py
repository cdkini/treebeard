"""Tests for `om index` — per-tag index note generation."""

from __future__ import annotations

import pathlib
import subprocess

from click.testing import CliRunner

from om.cli import cli
from tests.conftest import write_cfg


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


def commit_seed(vault: pathlib.Path) -> None:
    """Get the working tree clean so subsequent runs only see actual changes."""
    subprocess.run(["git", "add", "-A"], cwd=vault, check=True)
    subprocess.run(["git", "commit", "--quiet", "-m", "seed"], cwd=vault, check=True)


def test_help(runner: CliRunner) -> None:
    result = runner.invoke(cli, ["index", "--help"])
    assert result.exit_code == 0, result.output
    assert "index" in result.output


def test_below_threshold_no_index(
    runner: CliRunner,
    cfg_dir: pathlib.Path,
    vault: pathlib.Path,
    freeze_now: list,
) -> None:
    del freeze_now
    seed_note(vault, "a", "Alpha", ["foo"])
    seed_note(vault, "b", "Beta", ["foo"])
    commit_seed(vault)
    write_cfg(cfg_dir, vault)

    result = runner.invoke(cli, ["index", "--config-dir", str(cfg_dir)])

    assert result.exit_code == 0, result.output
    assert "wrote 0, updated 0, unchanged 0, skipped 0, stale 0" in result.output
    assert not (vault / "foo.md").exists()


def test_at_threshold_writes_index(
    runner: CliRunner,
    cfg_dir: pathlib.Path,
    vault: pathlib.Path,
    freeze_now: list,
) -> None:
    del freeze_now
    seed_note(vault, "a", "Alpha", ["foo"])
    seed_note(vault, "b", "Beta", ["foo"])
    seed_note(vault, "g", "gamma", ["foo"])
    commit_seed(vault)
    write_cfg(cfg_dir, vault)

    result = runner.invoke(cli, ["index", "--config-dir", str(cfg_dir)])

    assert result.exit_code == 0, result.output
    assert "wrote 1" in result.output
    path = vault / "foo.md"
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
    assert path.read_text(encoding="utf-8") == expected


def test_idempotent_second_run(
    runner: CliRunner,
    cfg_dir: pathlib.Path,
    vault: pathlib.Path,
    freeze_now: list,
) -> None:
    del freeze_now
    seed_note(vault, "a", "Alpha", ["foo"])
    seed_note(vault, "b", "Beta", ["foo"])
    seed_note(vault, "g", "gamma", ["foo"])
    commit_seed(vault)
    write_cfg(cfg_dir, vault)

    r1 = runner.invoke(cli, ["index", "--config-dir", str(cfg_dir)])
    assert r1.exit_code == 0, r1.output
    sha_after_first = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=vault, capture_output=True, text=True, check=True
    ).stdout.strip()

    r2 = runner.invoke(cli, ["index", "--config-dir", str(cfg_dir)])
    assert r2.exit_code == 0, r2.output
    assert "unchanged 1" in r2.output
    sha_after_second = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=vault, capture_output=True, text=True, check=True
    ).stdout.strip()
    assert sha_after_first == sha_after_second  # no spurious commit


def test_add_note_updates_index(
    runner: CliRunner,
    cfg_dir: pathlib.Path,
    vault: pathlib.Path,
    freeze_now: list,
) -> None:
    """After adding a 4th note, the index updates: created_at preserved,
    updated_at advanced, body includes the new entry alphabetically."""
    from datetime import UTC, datetime

    earlier = datetime(2026, 5, 1, 0, 0, 0, tzinfo=UTC)
    later = datetime(2026, 5, 7, 14, 23, 5, tzinfo=UTC)
    freeze_now[:] = [earlier, later]

    seed_note(vault, "a", "Alpha", ["foo"])
    seed_note(vault, "b", "Beta", ["foo"])
    seed_note(vault, "g", "gamma", ["foo"])
    commit_seed(vault)
    write_cfg(cfg_dir, vault)

    r1 = runner.invoke(cli, ["index", "--config-dir", str(cfg_dir)])
    assert r1.exit_code == 0, r1.output

    seed_note(vault, "d", "Delta", ["foo"])
    r2 = runner.invoke(cli, ["index", "--config-dir", str(cfg_dir)])

    assert r2.exit_code == 0, r2.output
    assert "updated 1" in r2.output
    text = (vault / "foo.md").read_text(encoding="utf-8")
    assert "created_at: 2026-05-01T00:00:00Z\n" in text
    assert "updated_at: 2026-05-07T14:23:05Z\n" in text
    body_start = text.index("---\n", 4) + len("---\n")
    assert text[body_start:] == "\n- [[Alpha]]\n- [[Beta]]\n- [[Delta]]\n- [[gamma]]\n"


def test_stale_index_warns(
    runner: CliRunner,
    cfg_dir: pathlib.Path,
    vault: pathlib.Path,
    freeze_now: list,
) -> None:
    del freeze_now
    seed_note(vault, "a", "Alpha", ["foo"])
    seed_note(vault, "b", "Beta", ["foo"])
    seed_note(vault, "g", "gamma", ["foo"])
    commit_seed(vault)
    write_cfg(cfg_dir, vault)

    r1 = runner.invoke(cli, ["index", "--config-dir", str(cfg_dir)])
    assert r1.exit_code == 0, r1.output
    index_before = (vault / "foo.md").read_text(encoding="utf-8")

    # Drop tag from one note → only 2 references remain.
    seed_note(vault, "g", "gamma", [])
    r2 = runner.invoke(cli, ["index", "--config-dir", str(cfg_dir)])

    assert r2.exit_code == 0, r2.output
    assert "stale 1" in r2.output
    assert "warning: stale index foo.md" in result_output(r2)
    # File untouched.
    assert (vault / "foo.md").read_text(encoding="utf-8") == index_before


def test_index_notes_excluded_from_corpus(
    runner: CliRunner,
    cfg_dir: pathlib.Path,
    vault: pathlib.Path,
    freeze_now: list,
) -> None:
    """A note tagged `[foo, index]` must not appear in the foo index, and
    must not contribute to foo's count."""
    del freeze_now
    # Three real notes tagged foo (so foo IS eligible).
    seed_note(vault, "a", "Alpha", ["foo"])
    seed_note(vault, "b", "Beta", ["foo"])
    seed_note(vault, "g", "gamma", ["foo"])
    # One pseudo-index note also tagged foo — must be ignored.
    seed_note(vault, "z", "Zeta", ["foo", "index"])
    commit_seed(vault)
    write_cfg(cfg_dir, vault)

    result = runner.invoke(cli, ["index", "--config-dir", str(cfg_dir)])

    assert result.exit_code == 0, result.output
    body = (vault / "foo.md").read_text(encoding="utf-8")
    assert "[[Zeta]]" not in body
    assert body.count("- [[") == 3


def test_collision_with_handwritten_note(
    runner: CliRunner,
    cfg_dir: pathlib.Path,
    vault: pathlib.Path,
    freeze_now: list,
) -> None:
    """A pre-existing `foo.md` not tagged `index` must not be clobbered."""
    del freeze_now
    seed_note(vault, "foo", "foo", ["bar"], body="my hand-written notes\n")
    seed_note(vault, "a", "Alpha", ["foo"])
    seed_note(vault, "b", "Beta", ["foo"])
    seed_note(vault, "g", "gamma", ["foo"])
    commit_seed(vault)
    write_cfg(cfg_dir, vault)
    original = (vault / "foo.md").read_text(encoding="utf-8")

    result = runner.invoke(cli, ["index", "--config-dir", str(cfg_dir)])

    assert result.exit_code == 0, result.output
    assert "skipped 1" in result.output
    assert "is not an index note" in result_output(result)
    assert (vault / "foo.md").read_text(encoding="utf-8") == original


def test_slugified_tag_filename(
    runner: CliRunner,
    cfg_dir: pathlib.Path,
    vault: pathlib.Path,
    freeze_now: list,
) -> None:
    """A tag with mixed case / non-alnum chars produces a slugified filename
    and the post-edit sweep does not try to rename it."""
    del freeze_now
    seed_note(vault, "a", "Alpha", ["Q2-2026"])
    seed_note(vault, "b", "Beta", ["Q2-2026"])
    seed_note(vault, "g", "gamma", ["Q2-2026"])
    commit_seed(vault)
    write_cfg(cfg_dir, vault)

    result = runner.invoke(cli, ["index", "--config-dir", str(cfg_dir)])

    assert result.exit_code == 0, result.output
    assert (vault / "q2-2026.md").exists()
    text = (vault / "q2-2026.md").read_text(encoding="utf-8")
    assert "title: Q2-2026\n" in text


def test_daily_notes_included(
    runner: CliRunner,
    cfg_dir: pathlib.Path,
    vault: pathlib.Path,
    freeze_now: list,
) -> None:
    """Daily-tagged notes count toward the daily index — useful as a
    journal TOC."""
    del freeze_now
    seed_note(vault, "2026-05-05", "2026-05-05", ["daily"])
    seed_note(vault, "2026-05-06", "2026-05-06", ["daily"])
    seed_note(vault, "2026-05-07", "2026-05-07", ["daily"])
    commit_seed(vault)
    write_cfg(cfg_dir, vault)

    result = runner.invoke(cli, ["index", "--config-dir", str(cfg_dir)])

    assert result.exit_code == 0, result.output
    body = (vault / "daily.md").read_text(encoding="utf-8")
    assert "[[2026-05-05]]" in body
    assert "[[2026-05-06]]" in body
    assert "[[2026-05-07]]" in body


def test_auto_commit_subject(
    runner: CliRunner,
    cfg_dir: pathlib.Path,
    vault: pathlib.Path,
    freeze_now: list,
) -> None:
    del freeze_now
    seed_note(vault, "a", "Alpha", ["foo"])
    seed_note(vault, "b", "Beta", ["foo"])
    seed_note(vault, "g", "gamma", ["foo"])
    commit_seed(vault)
    write_cfg(cfg_dir, vault)

    result = runner.invoke(cli, ["index", "--config-dir", str(cfg_dir)])

    assert result.exit_code == 0, result.output
    subject = subprocess.run(
        ["git", "log", "-1", "--format=%s"],
        cwd=vault,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert subject.startswith("index: ")


def test_errors_when_no_vault_configured(runner: CliRunner, tmp_path: pathlib.Path) -> None:
    empty_cfg = tmp_path / "empty"
    empty_cfg.mkdir()
    result = runner.invoke(cli, ["index", "--config-dir", str(empty_cfg)])
    assert result.exit_code != 0
    assert "no vault configured" in result.output


def result_output(result: object) -> str:
    """Combined stdout+stderr for assertions on warnings.

    `CliRunner` captures stderr separately from stdout when
    `mix_stderr=False`; the default mixes them, so `result.output`
    already contains both. We expose this as a helper to make the
    intent explicit at call sites.
    """
    return result.output  # type: ignore[attr-defined]
