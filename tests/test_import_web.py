"""Tests for `tb import web`."""

from __future__ import annotations

import pathlib
import subprocess
from collections.abc import Callable
from datetime import UTC, datetime

import httpx
import pytest
from click.testing import CliRunner

from treebeard.cli import cli
from treebeard.commands import import_ as import_cmd
from treebeard.config import Config
from treebeard.importers import web as web_importer_mod
from treebeard.importers.web import WebImporter

ARTICLE_HTML = """\
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Markdown</title>
  <meta property="og:title" content="Markdown — A Lightweight Markup Language">
</head>
<body>
  <header><nav>Skip nav</nav></header>
  <main>
    <article>
      <h1>Markdown</h1>
      <p>Markdown is a lightweight markup language for creating formatted text
         using a plain-text editor.</p>
      <p>John Gruber and Aaron Swartz created Markdown in 2004 as an easy-to-read
         markup language.</p>
      <p>It is now widely used in software documentation, README files, and online
         forums.</p>
    </article>
  </main>
  <footer>Footer junk</footer>
</body>
</html>
"""


def commit_seed(vault: pathlib.Path) -> None:
    subprocess.run(["git", "add", "-A"], cwd=vault, check=True)
    if subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=vault).returncode == 0:
        return
    subprocess.run(["git", "commit", "--quiet", "-m", "seed"], cwd=vault, check=True)


def seed_default_config(vault: pathlib.Path) -> None:
    """Write `config.toml` to the sandboxed default config dir.

    The autouse `_sandbox_default_config_dir` fixture in `conftest.py`
    redirects `DEFAULT_CONFIG_DIR` to a tmp path so the CLI cannot reach
    the developer's real `~/.treebeard`."""
    Config(vault=vault).save()


def install_fake_importer(
    monkeypatch: pytest.MonkeyPatch,
    handler: Callable[[httpx.Request], httpx.Response],
    *,
    now: datetime,
) -> None:
    """Patch the click command to use a `WebImporter` whose underlying
    `httpx.Client` is wired to an `httpx.MockTransport`. Production
    code path stays real — only the wire is faked."""
    transport = httpx.MockTransport(handler)

    def factory(*, url: str, now: datetime = now) -> WebImporter:
        client = httpx.Client(
            follow_redirects=True,
            transport=transport,
        )
        return WebImporter(url=url, now=now, client=client)

    # `WebImporter` is now imported lazily inside the `web` subcommand to
    # keep `tb` startup snappy, so we patch the source module instead of a
    # module-level alias.
    monkeypatch.setattr(web_importer_mod, "WebImporter", factory)
    monkeypatch.setattr(import_cmd, "_now_utc", lambda: now)


def html_response(body: str = ARTICLE_HTML, *, status: int = 200) -> httpx.Response:
    return httpx.Response(
        status,
        content=body.encode(),
        headers={"content-type": "text/html; charset=utf-8"},
    )


def test_help(runner: CliRunner) -> None:
    result = runner.invoke(cli, ["import", "web", "--help"])
    assert result.exit_code == 0, result.output
    assert "Import a web page" in result.output


def test_first_import_writes_note(
    runner: CliRunner,
    vault: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seed_default_config(vault)
    now = datetime(2026, 5, 9, 12, 0, 0, tzinfo=UTC)

    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return html_response()

    install_fake_importer(monkeypatch, handler, now=now)

    result = runner.invoke(cli, ["import", "web", "https://en.wikipedia.org/wiki/Markdown"])

    assert result.exit_code == 0, result.output
    assert "wrote 1, updated 0, unchanged 0, skipped 0" in result.output

    path = vault / "web-2026-05-09-markdown-a-lightweight-markup-language.md"
    text = path.read_text(encoding="utf-8")
    assert "title: Markdown — A Lightweight Markup Language" in text
    assert "source: import" in text
    assert "import_source: web" in text
    assert "import_id: https://en.wikipedia.org/wiki/Markdown" in text
    assert "import_url: https://en.wikipedia.org/wiki/Markdown" in text
    assert "tags: [web]" in text
    assert "created_at: 2026-05-09T12:00:00Z" in text
    assert "updated_at: 2026-05-09T12:00:00Z" in text
    assert "lightweight markup language" in text


def test_rerun_overwrites_body(
    runner: CliRunner,
    vault: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Re-running on the same URL re-fetches and overwrites the body
    (preserving `created_at`). Explicit re-import contract."""
    seed_default_config(vault)
    body_v1 = ARTICLE_HTML
    body_v2 = ARTICLE_HTML.replace(
        "It is now widely used", "It is now extremely widely used everywhere"
    )

    state = {"calls": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        del request
        state["calls"] += 1
        return html_response(body_v1 if state["calls"] == 1 else body_v2)

    now1 = datetime(2026, 5, 9, 12, 0, 0, tzinfo=UTC)
    install_fake_importer(monkeypatch, handler, now=now1)

    r1 = runner.invoke(cli, ["import", "web", "https://example.com/article"])
    assert r1.exit_code == 0, r1.output
    assert "wrote 1" in r1.output

    now2 = datetime(2026, 5, 9, 13, 0, 0, tzinfo=UTC)
    install_fake_importer(monkeypatch, handler, now=now2)

    r2 = runner.invoke(cli, ["import", "web", "https://example.com/article"])
    assert r2.exit_code == 0, r2.output
    assert "wrote 0, updated 1, unchanged 0, skipped 0" in r2.output

    path = vault / "web-2026-05-09-markdown-a-lightweight-markup-language.md"
    text = path.read_text(encoding="utf-8")
    assert "created_at: 2026-05-09T12:00:00Z" in text
    assert "updated_at: 2026-05-09T13:00:00Z" in text
    assert "extremely widely used everywhere" in text


def test_title_falls_back_to_h1(
    runner: CliRunner,
    vault: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No og:title and no <title> → use first <h1>."""
    seed_default_config(vault)
    html = """\
<!DOCTYPE html>
<html><head></head><body>
<article>
<h1>Hello From The H1</h1>
<p>This is a long enough article body to satisfy the trafilatura extractor.
We need several sentences to make sure trafilatura is willing to extract content.
The body should contain enough prose to count as the main content of the page.
Lorem ipsum dolor sit amet, consectetur adipiscing elit, sed do eiusmod tempor.</p>
</article>
</body></html>
"""

    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return html_response(html)

    now = datetime(2026, 5, 9, 12, 0, 0, tzinfo=UTC)
    install_fake_importer(monkeypatch, handler, now=now)

    result = runner.invoke(cli, ["import", "web", "https://example.com/p"])
    assert result.exit_code == 0, result.output
    assert "wrote 1" in result.output
    assert (vault / "web-2026-05-09-hello-from-the-h1.md").exists()


def test_url_canonicalization_collapses_variants(
    runner: CliRunner,
    vault: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`https://Example.COM/foo/#frag` and `https://example.com/foo` map to
    the same `import_id`, so the second invocation is `updated`, not `wrote`."""
    seed_default_config(vault)

    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return html_response()

    now1 = datetime(2026, 5, 9, 12, 0, 0, tzinfo=UTC)
    install_fake_importer(monkeypatch, handler, now=now1)

    r1 = runner.invoke(cli, ["import", "web", "https://Example.COM/foo/#section"])
    assert r1.exit_code == 0, r1.output
    assert "wrote 1" in r1.output

    now2 = datetime(2026, 5, 9, 13, 0, 0, tzinfo=UTC)
    install_fake_importer(monkeypatch, handler, now=now2)

    r2 = runner.invoke(cli, ["import", "web", "https://example.com/foo"])
    assert r2.exit_code == 0, r2.output
    assert "wrote 0, updated 1, unchanged 0, skipped 0" in r2.output


def test_non_html_content_type_errors(
    runner: CliRunner,
    vault: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seed_default_config(vault)

    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(
            200,
            content=b"%PDF-1.4 ...",
            headers={"content-type": "application/pdf"},
        )

    now = datetime(2026, 5, 9, 12, 0, 0, tzinfo=UTC)
    install_fake_importer(monkeypatch, handler, now=now)

    result = runner.invoke(cli, ["import", "web", "https://example.com/file.pdf"])
    assert result.exit_code != 0
    assert "text/html" in result.output
    assert not list(vault.glob("web-*.md"))


def test_empty_extraction_errors(
    runner: CliRunner,
    vault: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Trafilatura returns None on a content-free page → command errors."""
    seed_default_config(vault)
    html = "<!DOCTYPE html><html><head><title>x</title></head><body></body></html>"

    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return html_response(html)

    now = datetime(2026, 5, 9, 12, 0, 0, tzinfo=UTC)
    install_fake_importer(monkeypatch, handler, now=now)

    result = runner.invoke(cli, ["import", "web", "https://example.com/empty"])
    assert result.exit_code != 0
    assert "could not extract" in result.output
    assert not list(vault.glob("web-*.md"))


def test_http_error_surfaces(
    runner: CliRunner,
    vault: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seed_default_config(vault)

    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(404, content=b"not found")

    now = datetime(2026, 5, 9, 12, 0, 0, tzinfo=UTC)
    install_fake_importer(monkeypatch, handler, now=now)

    result = runner.invoke(cli, ["import", "web", "https://example.com/404"])
    assert result.exit_code != 0
    assert "404" in result.output


def test_collision_with_handwritten_file_skips(
    runner: CliRunner,
    vault: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pre-existing file at target path → skip with warning, leave it alone."""
    seed_default_config(vault)
    pre = vault / "web-2026-05-09-markdown-a-lightweight-markup-language.md"
    pre.write_text("hand-written\n", encoding="utf-8")
    commit_seed(vault)

    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return html_response()

    now = datetime(2026, 5, 9, 12, 0, 0, tzinfo=UTC)
    install_fake_importer(monkeypatch, handler, now=now)

    result = runner.invoke(cli, ["import", "web", "https://example.com/article"])
    assert result.exit_code == 0, result.output
    assert "skipped 1" in result.output
    assert pre.read_text(encoding="utf-8") == "hand-written\n"


def test_auto_commit_subject(
    runner: CliRunner,
    vault: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seed_default_config(vault)

    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return html_response()

    now = datetime(2026, 5, 9, 12, 0, 0, tzinfo=UTC)
    install_fake_importer(monkeypatch, handler, now=now)

    result = runner.invoke(cli, ["import", "web", "https://example.com/article"])
    assert result.exit_code == 0, result.output
    subject = subprocess.run(
        ["git", "log", "-1", "--format=%s"],
        cwd=vault,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert subject.startswith("import: ")
