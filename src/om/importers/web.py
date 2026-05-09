"""Web-page importer — fetch a URL, extract its main content, land it as
a markdown note.

Implements the `Importer` Protocol with a single-shot shape: the user
supplies one URL on the CLI; `list_summaries` returns one synthetic
`NoteSummary`, and `fetch_one` does the actual HTTP GET + extraction.
The shared `sync()` driver still owns vault layout, slug collisions,
and idempotency by `import_id`.

`updated_at` is set to "now" on every run so the driver naturally takes
the overwrite branch when the note already exists — re-running
`om import web <URL>` always re-fetches the body. `created_at` is
preserved by the driver across overwrites.
"""

from __future__ import annotations

from datetime import datetime
from urllib.parse import urlsplit, urlunsplit

import httpx
import trafilatura
from bs4 import BeautifulSoup

from om.importers import ImportedNote, NoteSummary
from om.ui import OmError

DEFAULT_TIMEOUT = 30.0
USER_AGENT = "om/0.1 (+https://github.com/cdkini/omniscience)"


class WebImporter:
    """Implements the `Importer` Protocol for arbitrary web pages."""

    source = "web"
    single_shot = True

    def __init__(
        self,
        url: str,
        *,
        now: datetime,
        client: httpx.Client | None = None,
    ) -> None:
        self._canonical_url = _canonicalize(url)
        self._now = now
        if client is None:
            client = httpx.Client(
                follow_redirects=True,
                timeout=DEFAULT_TIMEOUT,
                headers={"User-Agent": USER_AGENT},
            )
        self._client = client

    def list_summaries(self, *, since: datetime) -> list[NoteSummary]:
        """Return a single summary for the user-supplied URL.

        `since` is part of the protocol but irrelevant here — a one-shot
        URL import has nothing to filter against. We set `updated_at` to
        "now" so the driver doesn't short-circuit `fetch_one` (it skips
        the fetch when summary.updated_at <= prior.updated_at).
        """
        del since
        return [
            NoteSummary(
                import_id=self._canonical_url,
                display_title=self._canonical_url,
                updated_at=self._now,
            )
        ]

    def fetch_one(self, summary: NoteSummary) -> ImportedNote:
        del summary
        try:
            response = self._client.get(self._canonical_url)
        except httpx.HTTPError as exc:
            raise OmError(f"failed to fetch {self._canonical_url}: {exc}") from exc
        if response.status_code >= 400:
            raise OmError(
                f"HTTP {response.status_code} fetching {self._canonical_url}: {response.text[:200]}"
            )

        ctype = response.headers.get("content-type", "")
        if "text/html" not in ctype.lower():
            raise OmError(
                "only text/html is supported",
                hint=f"this URL returned {ctype!r}",
            )

        html = response.text
        body = trafilatura.extract(
            html,
            output_format="markdown",
            include_links=True,
            include_images=False,
            with_metadata=False,
        )
        if body is None or not body.strip():
            raise OmError(
                "could not extract article content",
                hint="page may be JS-rendered or have no main content",
            )

        title = _resolve_title(html, self._canonical_url)
        final_url = _canonicalize(str(response.url))

        return ImportedNote(
            import_id=self._canonical_url,
            import_url=final_url,
            title=title,
            created_at=self._now,
            updated_at=self._now,
            body_markdown="\n" + body.rstrip() + "\n",
            tags=["web"],
        )


def _canonicalize(url: str) -> str:
    """Lowercase scheme+host, drop fragment, strip trailing slash on path.

    Keeps the query string and path case intact (paths and query keys
    are sometimes case-sensitive). The goal is to fold trivial URL
    variations (`HTTPS://`, `#fragment`, trailing `/`) so re-importing
    `https://Example.COM/foo/#x` and `https://example.com/foo` lands on
    the same note.
    """
    parts = urlsplit(url)
    scheme = parts.scheme.lower()
    netloc = parts.netloc.lower()
    path = parts.path
    if len(path) > 1 and path.endswith("/"):
        path = path.rstrip("/")
    return urlunsplit((scheme, netloc, path, parts.query, ""))


def _resolve_title(html: str, fallback_url: str) -> str:
    """og:title → <title> → first <h1> → URL."""
    metadata = trafilatura.extract_metadata(html)
    if metadata is not None:
        candidate = (metadata.title or "").strip()
        if candidate:
            return candidate

    soup = BeautifulSoup(html, "html.parser")
    h1 = soup.find("h1")
    if h1 is not None:
        candidate = h1.get_text(strip=True)
        if candidate:
            return candidate

    return fallback_url
