"""Tests for `om import granola`."""

from __future__ import annotations

import json
import pathlib
import subprocess
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
from click.testing import CliRunner

from om.cli import cli
from om.commands import import_ as import_cmd
from om.importers.granola import BASE_URL, GranolaImporter
from tests.conftest import write_cfg


def commit_seed(vault: pathlib.Path) -> None:
    """Make subsequent runs see only actual changes."""
    subprocess.run(["git", "add", "-A"], cwd=vault, check=True)
    if subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=vault).returncode == 0:
        return
    subprocess.run(["git", "commit", "--quiet", "-m", "seed"], cwd=vault, check=True)


def head_sha(vault: pathlib.Path) -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=vault,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


def fake_note(
    note_id: str,
    title: str | None,
    *,
    created_at: str = "2026-05-07T14:00:00Z",
    updated_at: str = "2026-05-07T14:30:00Z",
    summary: str = "Discussed the roadmap.",
    transcript: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build a Granola GET /notes/{id} payload."""
    if transcript is None:
        transcript = [
            {
                "speaker": {
                    "source": "microphone",
                    "diarization_label": "Sarah",
                },
                "text": "Hey there.",
                "start_time": created_at,
                "end_time": updated_at,
            },
        ]
    return {
        "id": note_id,
        "object": "note",
        "title": title,
        "created_at": created_at,
        "updated_at": updated_at,
        "summary_markdown": summary,
        "summary_text": summary,
        "transcript": transcript,
        "web_url": f"https://notes.granola.ai/d/{note_id}",
    }


def install_fake_importer(
    monkeypatch: pytest.MonkeyPatch,
    notes_by_id: dict[str, dict[str, Any]],
    *,
    list_calls: list[dict[str, Any]] | None = None,
    get_note_calls: list[str] | None = None,
) -> None:
    """Patch the click command to use a `GranolaImporter` whose underlying
    `httpx.Client` is wired to an `httpx.MockTransport`. This keeps the
    production code path (real client, real serialization) and only
    fakes the wire."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/notes":
            params = dict(request.url.params)
            if list_calls is not None:
                list_calls.append(params)
            return httpx.Response(
                200,
                json={
                    "notes": [
                        {
                            "id": n["id"],
                            "object": "note",
                            "title": n["title"],
                            "created_at": n["created_at"],
                            "updated_at": n["updated_at"],
                        }
                        for n in notes_by_id.values()
                    ],
                    "hasMore": False,
                    "cursor": None,
                },
            )
        if request.url.path.startswith("/v1/notes/"):
            note_id = request.url.path.rsplit("/", 1)[-1]
            if get_note_calls is not None:
                get_note_calls.append(note_id)
            note = notes_by_id.get(note_id)
            if note is None:
                return httpx.Response(404, json={"error": "not found"})
            return httpx.Response(200, json=note)
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)

    def factory(api_key: str) -> GranolaImporter:
        del api_key
        client = httpx.Client(
            base_url=BASE_URL,
            headers={"Authorization": "Bearer fake"},
            transport=transport,
        )
        return GranolaImporter(api_key="fake", client=client)

    monkeypatch.setattr(import_cmd, "GranolaImporter", factory)


@pytest.fixture
def cfg_with_granola(cfg_dir: pathlib.Path, vault: pathlib.Path) -> Callable[[], None]:
    """Seed config + a granola_api_key entry under [secrets]."""

    def _seed() -> None:
        write_cfg(cfg_dir, vault)
        path = cfg_dir / "config.toml"
        text = path.read_text(encoding="utf-8")
        path.write_text(
            text.replace(
                'granola_api_key = ""',
                'granola_api_key = "grn_test_key"',
            ),
            encoding="utf-8",
        )

    return _seed


def test_help(runner: CliRunner) -> None:
    result = runner.invoke(cli, ["import", "granola", "--help"])
    assert result.exit_code == 0, result.output
    assert "Import meeting notes from Granola" in result.output


def test_first_import_writes_notes(
    runner: CliRunner,
    cfg_dir: pathlib.Path,
    vault: pathlib.Path,
    cfg_with_granola: Callable[[], None],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg_with_granola()
    notes = {
        "not_aaaaaaaaaaaaaa": fake_note("not_aaaaaaaaaaaaaa", "Roadmap sync"),
        "not_bbbbbbbbbbbbbb": fake_note(
            "not_bbbbbbbbbbbbbb",
            "1:1 with Sarah",
            created_at="2026-05-08T09:00:00Z",
            updated_at="2026-05-08T09:30:00Z",
        ),
    }
    install_fake_importer(monkeypatch, notes)

    result = runner.invoke(cli, ["import", "granola", "--config-dir", str(cfg_dir)])

    assert result.exit_code == 0, result.output
    assert "wrote 2, updated 0, unchanged 0, skipped 0" in result.output

    a = (vault / "granola-2026-05-07-roadmap-sync.md").read_text(encoding="utf-8")
    assert "source: import" in a
    assert "import_source: granola" in a
    assert "import_id: not_aaaaaaaaaaaaaa" in a
    assert "import_url: https://notes.granola.ai/d/not_aaaaaaaaaaaaaa" in a
    assert "tags: [granola]" in a
    assert "Discussed the roadmap." in a
    assert "## Transcript" in a
    assert "**Sarah:** Hey there." in a

    b = (vault / "granola-2026-05-08-1-1-with-sarah.md").read_text(encoding="utf-8")
    assert "import_id: not_bbbbbbbbbbbbbb" in b


def test_idempotent_second_run(
    runner: CliRunner,
    cfg_dir: pathlib.Path,
    vault: pathlib.Path,
    cfg_with_granola: Callable[[], None],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg_with_granola()
    notes = {"not_aaaaaaaaaaaaaa": fake_note("not_aaaaaaaaaaaaaa", "Roadmap sync")}
    get_note_calls: list[str] = []
    install_fake_importer(monkeypatch, notes, get_note_calls=get_note_calls)

    r1 = runner.invoke(cli, ["import", "granola", "--config-dir", str(cfg_dir)])
    assert r1.exit_code == 0, r1.output
    sha1 = head_sha(vault)
    assert get_note_calls == ["not_aaaaaaaaaaaaaa"]  # initial import fetched the body

    r2 = runner.invoke(cli, ["import", "granola", "--config-dir", str(cfg_dir)])
    assert r2.exit_code == 0, r2.output
    assert "wrote 0, updated 0, unchanged 1, skipped 0" in r2.output
    sha2 = head_sha(vault)
    assert sha1 == sha2  # no spurious commit
    # Optimization: re-run skipped fetch_one for the unchanged note.
    assert get_note_calls == ["not_aaaaaaaaaaaaaa"]


def test_subsecond_updated_at_does_not_force_update(
    runner: CliRunner,
    cfg_dir: pathlib.Path,
    vault: pathlib.Path,
    cfg_with_granola: Callable[[], None],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Granola can return microsecond-precision timestamps. Frontmatter
    serializes at second resolution; if the importer kept microseconds,
    every re-run would see `summary.updated_at > local.updated_at` and
    needlessly re-fetch + rewrite the note. Truncating to seconds keeps
    idempotency honest."""
    cfg_with_granola()
    notes = {
        "not_aaaaaaaaaaaaaa": fake_note(
            "not_aaaaaaaaaaaaaa",
            "Roadmap sync",
            updated_at="2026-05-07T14:30:00.123456Z",
        ),
    }
    get_note_calls: list[str] = []
    install_fake_importer(monkeypatch, notes, get_note_calls=get_note_calls)

    r1 = runner.invoke(cli, ["import", "granola", "--config-dir", str(cfg_dir)])
    assert r1.exit_code == 0, r1.output
    assert "wrote 1" in r1.output

    r2 = runner.invoke(cli, ["import", "granola", "--config-dir", str(cfg_dir)])
    assert r2.exit_code == 0, r2.output
    assert "unchanged 1" in r2.output
    # And — critically — fetch_one must not be called the second time.
    assert get_note_calls == ["not_aaaaaaaaaaaaaa"]


def test_remote_update_overwrites_preserving_created_at(
    runner: CliRunner,
    cfg_dir: pathlib.Path,
    vault: pathlib.Path,
    cfg_with_granola: Callable[[], None],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg_with_granola()
    note = fake_note(
        "not_aaaaaaaaaaaaaa",
        "Roadmap sync",
        created_at="2026-05-07T14:00:00Z",
        updated_at="2026-05-07T14:30:00Z",
        summary="First version.",
    )
    notes = {"not_aaaaaaaaaaaaaa": note}
    install_fake_importer(monkeypatch, notes)

    r1 = runner.invoke(cli, ["import", "granola", "--config-dir", str(cfg_dir)])
    assert r1.exit_code == 0, r1.output

    # Granola edits the note: updated_at advances, summary changes.
    notes["not_aaaaaaaaaaaaaa"] = fake_note(
        "not_aaaaaaaaaaaaaa",
        "Roadmap sync",
        created_at="2026-05-07T14:00:00Z",
        updated_at="2026-05-07T15:45:00Z",
        summary="Second version with edits.",
    )

    r2 = runner.invoke(cli, ["import", "granola", "--config-dir", str(cfg_dir)])
    assert r2.exit_code == 0, r2.output
    assert "updated 1" in r2.output

    text = (vault / "granola-2026-05-07-roadmap-sync.md").read_text(encoding="utf-8")
    assert "created_at: 2026-05-07T14:00:00Z" in text  # preserved
    assert "updated_at: 2026-05-07T15:45:00Z" in text  # advanced
    assert "Second version with edits." in text
    assert "First version." not in text


def test_local_newer_is_no_op(
    runner: CliRunner,
    cfg_dir: pathlib.Path,
    vault: pathlib.Path,
    cfg_with_granola: Callable[[], None],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If a local imported note has updated_at >= remote, skip it."""
    cfg_with_granola()
    note = fake_note(
        "not_aaaaaaaaaaaaaa",
        "Roadmap sync",
        updated_at="2026-05-07T14:30:00Z",
    )
    notes = {"not_aaaaaaaaaaaaaa": note}
    install_fake_importer(monkeypatch, notes)

    r1 = runner.invoke(cli, ["import", "granola", "--config-dir", str(cfg_dir)])
    assert r1.exit_code == 0, r1.output

    # User locally edits the file and bumps updated_at past the remote.
    path = vault / "granola-2026-05-07-roadmap-sync.md"
    text = path.read_text(encoding="utf-8")
    path.write_text(
        text.replace(
            "updated_at: 2026-05-07T14:30:00Z",
            "updated_at: 2027-01-01T00:00:00Z",
        ),
        encoding="utf-8",
    )
    commit_seed(vault)
    before = path.read_text(encoding="utf-8")

    r2 = runner.invoke(cli, ["import", "granola", "--config-dir", str(cfg_dir)])
    assert r2.exit_code == 0, r2.output
    assert "unchanged 1" in r2.output
    assert path.read_text(encoding="utf-8") == before


def test_recurring_meeting_lands_per_day(
    runner: CliRunner,
    cfg_dir: pathlib.Path,
    vault: pathlib.Path,
    cfg_with_granola: Callable[[], None],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Same title on different days → both land (filenames carry the date)."""
    cfg_with_granola()
    notes = {
        "not_aaaaaaaaaaaaaa": fake_note(
            "not_aaaaaaaaaaaaaa",
            "Standup",
            created_at="2026-05-07T09:00:00Z",
            updated_at="2026-05-07T09:30:00Z",
        ),
        "not_bbbbbbbbbbbbbb": fake_note(
            "not_bbbbbbbbbbbbbb",
            "Standup",
            created_at="2026-05-08T09:00:00Z",
            updated_at="2026-05-08T09:30:00Z",
        ),
    }
    install_fake_importer(monkeypatch, notes)

    result = runner.invoke(cli, ["import", "granola", "--config-dir", str(cfg_dir)])
    assert result.exit_code == 0, result.output
    assert "wrote 2, updated 0, unchanged 0, skipped 0" in result.output
    assert (vault / "granola-2026-05-07-standup.md").exists()
    assert (vault / "granola-2026-05-08-standup.md").exists()


def test_collision_with_handwritten_file_skips(
    runner: CliRunner,
    cfg_dir: pathlib.Path,
    vault: pathlib.Path,
    cfg_with_granola: Callable[[], None],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A pre-existing file at the target path (e.g. someone hand-wrote one
    with our prefix) is left alone — skip with a warning."""
    cfg_with_granola()
    pre = vault / "granola-2026-05-07-roadmap-sync.md"
    pre.write_text("hand-written\n", encoding="utf-8")
    commit_seed(vault)
    notes = {"not_aaaaaaaaaaaaaa": fake_note("not_aaaaaaaaaaaaaa", "Roadmap sync")}
    install_fake_importer(monkeypatch, notes)

    result = runner.invoke(cli, ["import", "granola", "--config-dir", str(cfg_dir)])

    assert result.exit_code == 0, result.output
    assert "skipped 1" in result.output
    assert "already exists" in result.output
    assert pre.read_text(encoding="utf-8") == "hand-written\n"


def test_missing_api_key_errors(
    runner: CliRunner,
    cfg_dir: pathlib.Path,
    vault: pathlib.Path,
) -> None:
    write_cfg(cfg_dir, vault)
    # Default config has empty granola_api_key.
    result = runner.invoke(cli, ["import", "granola", "--config-dir", str(cfg_dir)])
    assert result.exit_code != 0
    assert "granola_api_key not set" in result.output


def test_auto_commit_subject(
    runner: CliRunner,
    cfg_dir: pathlib.Path,
    vault: pathlib.Path,
    cfg_with_granola: Callable[[], None],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg_with_granola()
    notes = {"not_aaaaaaaaaaaaaa": fake_note("not_aaaaaaaaaaaaaa", "Roadmap sync")}
    install_fake_importer(monkeypatch, notes)

    result = runner.invoke(cli, ["import", "granola", "--config-dir", str(cfg_dir)])
    assert result.exit_code == 0, result.output
    subject = subprocess.run(
        ["git", "log", "-1", "--format=%s"],
        cwd=vault,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert subject.startswith("import: ")


def test_since_flag_passes_to_api(
    runner: CliRunner,
    cfg_dir: pathlib.Path,
    vault: pathlib.Path,
    cfg_with_granola: Callable[[], None],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg_with_granola()
    list_calls: list[dict[str, Any]] = []
    install_fake_importer(monkeypatch, {}, list_calls=list_calls)

    result = runner.invoke(
        cli,
        [
            "import",
            "granola",
            "--config-dir",
            str(cfg_dir),
            "--since",
            "2026-01-01",
        ],
    )
    assert result.exit_code == 0, result.output
    assert len(list_calls) == 1
    assert list_calls[0]["updated_after"] == "2026-01-01T00:00:00Z"


def test_default_since_is_seven_days_ago(
    runner: CliRunner,
    cfg_dir: pathlib.Path,
    vault: pathlib.Path,
    cfg_with_granola: Callable[[], None],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg_with_granola()
    monkeypatch.setattr(import_cmd, "_now_utc", lambda: datetime(2026, 5, 9, 12, 0, 0, tzinfo=UTC))
    list_calls: list[dict[str, Any]] = []
    install_fake_importer(monkeypatch, {}, list_calls=list_calls)

    result = runner.invoke(cli, ["import", "granola", "--config-dir", str(cfg_dir)])
    assert result.exit_code == 0, result.output
    assert list_calls[0]["updated_after"] == "2026-05-02T12:00:00Z"


def test_pagination_follows_cursor(
    runner: CliRunner,
    cfg_dir: pathlib.Path,
    vault: pathlib.Path,
    cfg_with_granola: Callable[[], None],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two-page list response → both notes imported, cursor passed back."""
    cfg_with_granola()

    note_a = fake_note("not_aaaaaaaaaaaaaa", "First note")
    note_b = fake_note(
        "not_bbbbbbbbbbbbbb",
        "Second note",
        created_at="2026-05-08T09:00:00Z",
        updated_at="2026-05-08T09:30:00Z",
    )

    pages = [
        {
            "notes": [
                {
                    "id": note_a["id"],
                    "object": "note",
                    "title": note_a["title"],
                    "created_at": note_a["created_at"],
                    "updated_at": note_a["updated_at"],
                }
            ],
            "hasMore": True,
            "cursor": "page2",
        },
        {
            "notes": [
                {
                    "id": note_b["id"],
                    "object": "note",
                    "title": note_b["title"],
                    "created_at": note_b["created_at"],
                    "updated_at": note_b["updated_at"],
                }
            ],
            "hasMore": False,
            "cursor": None,
        },
    ]
    list_calls: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/notes":
            params = dict(request.url.params)
            list_calls.append(params)
            page_idx = 1 if params.get("cursor") == "page2" else 0
            return httpx.Response(200, json=pages[page_idx])
        if request.url.path.startswith("/v1/notes/"):
            note_id = request.url.path.rsplit("/", 1)[-1]
            note = note_a if note_id == note_a["id"] else note_b
            return httpx.Response(200, json=note)
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)

    def factory(api_key: str) -> GranolaImporter:
        del api_key
        client = httpx.Client(
            base_url=BASE_URL,
            headers={"Authorization": "Bearer fake"},
            transport=transport,
        )
        return GranolaImporter(api_key="fake", client=client)

    monkeypatch.setattr(import_cmd, "GranolaImporter", factory)

    result = runner.invoke(cli, ["import", "granola", "--config-dir", str(cfg_dir)])
    assert result.exit_code == 0, result.output
    assert "wrote 2" in result.output
    assert (vault / "granola-2026-05-07-first-note.md").exists()
    assert (vault / "granola-2026-05-08-second-note.md").exists()
    # First call has no cursor; second call passes "page2".
    assert "cursor" not in list_calls[0]
    assert list_calls[1]["cursor"] == "page2"


def test_null_title_falls_back(
    runner: CliRunner,
    cfg_dir: pathlib.Path,
    vault: pathlib.Path,
    cfg_with_granola: Callable[[], None],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A null title gets a deterministic fallback so the file lands somewhere."""
    cfg_with_granola()
    notes = {
        "not_aaaaaaaaaaaaaa": fake_note(
            "not_aaaaaaaaaaaaaa",
            None,
            created_at="2026-05-07T14:00:00Z",
        ),
    }
    install_fake_importer(monkeypatch, notes)

    result = runner.invoke(cli, ["import", "granola", "--config-dir", str(cfg_dir)])
    assert result.exit_code == 0, result.output
    assert "wrote 1" in result.output
    assert (vault / "granola-2026-05-07-untitled-meeting.md").exists()


def test_api_401_surfaces_clear_error(
    runner: CliRunner,
    cfg_dir: pathlib.Path,
    vault: pathlib.Path,
    cfg_with_granola: Callable[[], None],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg_with_granola()

    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(401, content=json.dumps({"error": "invalid"}).encode())

    transport = httpx.MockTransport(handler)

    def factory(api_key: str) -> GranolaImporter:
        del api_key
        client = httpx.Client(
            base_url=BASE_URL,
            headers={"Authorization": "Bearer bad"},
            transport=transport,
        )
        return GranolaImporter(api_key="bad", client=client)

    monkeypatch.setattr(import_cmd, "GranolaImporter", factory)

    result = runner.invoke(cli, ["import", "granola", "--config-dir", str(cfg_dir)])
    assert result.exit_code != 0
    assert "401" in result.output or "rejected" in result.output
