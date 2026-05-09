"""`om index` — generate per-tag index notes.

For each tag carried by at least `THRESHOLD` non-index notes, materialize a
`<vault>/<slug(tag)>.md` whose body is an alphabetical list of `[[Title]]`
links. Index notes are marked by `tags: [index]` so they're skipped during
the corpus scan and recognizable on subsequent runs.

Idempotent by design: if the desired body matches what's on disk, the file
is left alone (no `updated_at` bump, no spurious commit). Notes that exist
under a tag's filename but lack the `index` marker are treated as
hand-written and refused with a warning.
"""

from __future__ import annotations

import pathlib
from datetime import UTC, datetime

import click

from om.config import CONFIG_FILENAME, DEFAULT_CONFIG_DIR, load_config
from om.frontmatter import Frontmatter, Source, split_document
from om.post_edit import PostEditAbort, slugify

INDEX_TAG = "index"
THRESHOLD = 3


def _now_utc() -> datetime:
    return datetime.now(UTC)


@click.command("index")
@click.option(
    "--config-dir",
    "config_dir",
    type=click.Path(),
    default=None,
    help=f"Directory holding {CONFIG_FILENAME} (default: {DEFAULT_CONFIG_DIR}).",
)
@click.pass_context
def command(ctx: click.Context, config_dir: str | None) -> None:
    """Generate per-tag index notes for tags with >=3 references."""
    ctx.ensure_object(dict)["config_dir"] = config_dir
    cfg = load_config(config_dir)
    now = _now_utc()

    corpus = _scan_vault(cfg.vault)
    tag_to_titles = _group_by_tag(corpus)

    wrote = updated = unchanged = skipped = 0
    eligible_tags: set[str] = set()
    for tag, titles in sorted(tag_to_titles.items()):
        if len(titles) < THRESHOLD:
            continue
        eligible_tags.add(tag)
        result = _upsert_index(cfg.vault, tag, titles, now)
        if result == "wrote":
            wrote += 1
        elif result == "updated":
            updated += 1
        elif result == "unchanged":
            unchanged += 1
        else:
            skipped += 1

    stale = _find_stale(cfg.vault, eligible_tags)
    for path in stale:
        click.echo(
            f"warning: stale index {path.name} (tag dropped below threshold)",
            err=True,
        )

    click.echo(
        f"wrote {wrote}, updated {updated}, unchanged {unchanged}, "
        f"skipped {skipped}, stale {len(stale)}"
    )


def _scan_vault(vault: pathlib.Path) -> list[tuple[pathlib.Path, Frontmatter]]:
    """Return (path, frontmatter) for every parseable root note that isn't
    itself an index note."""
    out: list[tuple[pathlib.Path, Frontmatter]] = []
    for path in sorted(vault.glob("*.md")):
        parsed = split_document(path.read_text(encoding="utf-8"))
        if parsed is None:
            continue
        fm, _ = parsed
        if INDEX_TAG in fm.tags:
            continue
        out.append((path, fm))
    return out


def _group_by_tag(
    corpus: list[tuple[pathlib.Path, Frontmatter]],
) -> dict[str, list[str]]:
    """Group note titles by each tag they carry."""
    grouped: dict[str, list[str]] = {}
    for _, fm in corpus:
        for tag in fm.tags:
            grouped.setdefault(tag, []).append(fm.title)
    return grouped


def _build_body(titles: list[str]) -> str:
    """Alphabetical (case-insensitive), de-duplicated wikilink list,
    preceded by a blank line so the body breathes after the frontmatter."""
    unique = sorted(set(titles), key=str.lower)
    return "\n" + "".join(f"- [[{t}]]\n" for t in unique)


def _upsert_index(vault: pathlib.Path, tag: str, titles: list[str], now: datetime) -> str:
    """Create or update `<vault>/<slug(tag)>.md`. Returns one of
    "wrote" | "updated" | "unchanged" | "skipped"."""
    try:
        slug = slugify(tag)
    except PostEditAbort:
        click.echo(f"warning: tag {tag!r} produces an empty slug; skipping", err=True)
        return "skipped"
    path = vault / f"{slug}.md"
    desired_body = _build_body(titles)

    if path.exists():
        parsed = split_document(path.read_text(encoding="utf-8"))
        if parsed is None or INDEX_TAG not in parsed[0].tags:
            click.echo(
                f"warning: {path.name} exists but is not an index note; skipping",
                err=True,
            )
            return "skipped"
        old_fm, old_body = parsed
        if old_body == desired_body and old_fm.title == tag and old_fm.tags == [INDEX_TAG]:
            return "unchanged"
        new_fm = Frontmatter(
            title=tag,
            source=Source.USER,
            created_at=old_fm.created_at,
            updated_at=now,
            tags=[INDEX_TAG],
            extra=old_fm.extra,
        )
        path.write_text(new_fm.serialize() + desired_body, encoding="utf-8")
        return "updated"

    new_fm = Frontmatter.new(tag, now)
    new_fm.tags = [INDEX_TAG]
    path.write_text(new_fm.serialize() + desired_body, encoding="utf-8")
    return "wrote"


def _find_stale(vault: pathlib.Path, eligible_tags: set[str]) -> list[pathlib.Path]:
    """Existing index notes whose tag (= title) is no longer eligible."""
    stale: list[pathlib.Path] = []
    for path in sorted(vault.glob("*.md")):
        parsed = split_document(path.read_text(encoding="utf-8"))
        if parsed is None:
            continue
        fm, _ = parsed
        if fm.tags != [INDEX_TAG]:
            continue
        if fm.title not in eligible_tags:
            stale.append(path)
    return stale
