"""Post-edit invariants for note files.

`reconcile_filename` enforces title-canonical filenames: after the user
closes the editor, the file is renamed so its stem matches
`slugify(frontmatter.title)`. Empty titles fall back to a `scratch-*`
stem (and stay there until the user names the note).

Daily-tagged notes are protected — a rename would break `tb daily`'s
prior-day lookup, so we raise `PostEditAbort` and let the editor wrapper
restore the pre-edit snapshot.
"""

from __future__ import annotations

import pathlib
import re
from datetime import datetime

from treebeard.frontmatter import split_document

DAILY_TAG = "daily"
SCRATCH_PREFIX = "scratch-"
SCRATCH_TIMESTAMP_FMT = "%Y-%m-%dt%H-%M-%S"

_SLUG_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")


class PostEditAbort(Exception):
    """Raised by a post-edit step to trigger snapshot revert in the wrapper."""


def slugify(name: str) -> str:
    """Lowercase, strip a trailing `.md`, replace runs of non-alnum with `-`.

    Raises `PostEditAbort` if the result is empty — the caller decides
    whether that's user-facing or wrapped in a friendlier message.
    """
    lowered = name.lower()
    if lowered.endswith(".md"):
        lowered = lowered[:-3]
    slug = _SLUG_NON_ALNUM_RE.sub("-", lowered).strip("-")
    if not slug:
        raise PostEditAbort(f"name {name!r} produces an empty slug")
    return slug


def scratch_filename(now: datetime) -> str:
    """Return the `scratch-<timestamp>.md` filename for an untitled note."""
    return f"{SCRATCH_PREFIX}{now.strftime(SCRATCH_TIMESTAMP_FMT)}.md"


def reconcile_filename(path: pathlib.Path, *, now: datetime) -> pathlib.Path:
    """Rename `path` so its stem matches its frontmatter title.

    Returns the (possibly new) path. No-op if frontmatter is unparseable
    or the desired stem already matches.

    Raises `PostEditAbort` when:
      - the file is tagged `daily` and the title would force a rename
        (date filenames are load-bearing for carryover);
      - the desired filename already exists.

    Empty title behavior: an *already*-scratch file stays put (no churn
    on every save). A non-scratch file with an emptied title gets renamed
    to a fresh `scratch-<now>.md`. This is rare in practice but keeps the
    rule consistent.
    """
    contents = path.read_text(encoding="utf-8")
    parsed = split_document(contents)
    if parsed is None:
        return path
    fm, _ = parsed
    title = fm.title.strip()

    if title:
        desired_stem = slugify(title)
    elif path.stem.startswith(SCRATCH_PREFIX):
        # Untitled and already a scratch — leave it alone.
        return path
    else:
        desired_stem = scratch_filename(now).removesuffix(".md")

    if desired_stem == path.stem:
        return path

    if DAILY_TAG in fm.tags:
        raise PostEditAbort("daily note filename is protected")

    target = path.with_name(f"{desired_stem}.md")
    if target.exists():
        raise PostEditAbort(f"would rename to {target.name} but it exists")

    path.rename(target)
    return target
