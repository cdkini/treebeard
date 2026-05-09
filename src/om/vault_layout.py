"""Layout of `<vault>/.om/`, the per-vault state directory.

Distinct from `~/.om/`, the user-level config dir handled in `om.config`.
This module enumerates every known section under `<vault>/.om/` and
exposes pure path constructors for them. It is intentionally defensive:
no helper here creates directories, checks existence, or raises — section
owners (`om.archiver`, `om.chat`, ...) handle lazy `mkdir` on first
write, as they already do.

When adding a new `.om/` section, register it here first, then have the
owning module import the constructor. Don't hardcode `.om/<name>`
elsewhere.

Known sections today:
  - archive/        soft-deleted notes (owner: om.archiver)
  - conversations/  chat session JSONL transcripts (owner: om.chat)
"""

from __future__ import annotations

import pathlib

OM_DIRNAME = ".om"

ARCHIVE_DIRNAME = "archive"
CONVERSATIONS_DIRNAME = "conversations"


def om_dir(vault: pathlib.Path) -> pathlib.Path:
    return vault / OM_DIRNAME


def archive_dir(vault: pathlib.Path) -> pathlib.Path:
    return om_dir(vault) / ARCHIVE_DIRNAME


def conversations_dir(vault: pathlib.Path) -> pathlib.Path:
    return om_dir(vault) / CONVERSATIONS_DIRNAME


# Vault-relative form used by guard logic that compares string-form
# paths against archive membership (see `om.chat._path_targets_archive`).
# `PurePosixPath` so the displayed form stays `.om/archive` regardless
# of host OS.
ARCHIVE_REL = pathlib.PurePosixPath(OM_DIRNAME) / ARCHIVE_DIRNAME
