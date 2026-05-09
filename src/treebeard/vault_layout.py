"""Layout of `<vault>/.treebeard/`, the per-vault state directory.

Distinct from `~/.treebeard/`, the user-level config dir handled in `treebeard.config`.
This module enumerates every known section under `<vault>/.treebeard/` and
exposes pure path constructors for them. It is intentionally defensive:
no helper here creates directories, checks existence, or raises — section
owners (`treebeard.archiver`, `treebeard.chat`, ...) handle lazy `mkdir` on first
write, as they already do.

When adding a new `.treebeard/` section, register it here first, then have the
owning module import the constructor. Don't hardcode `.treebeard/<name>`
elsewhere.

Known sections today:
  - archive/        soft-deleted notes (owner: treebeard.archiver)
  - conversations/  chat session JSONL transcripts (owner: treebeard.chat)
"""

from __future__ import annotations

import pathlib

TREEBEARD_DIRNAME = ".treebeard"

ARCHIVE_DIRNAME = "archive"
CONVERSATIONS_DIRNAME = "conversations"


def treebeard_dir(vault: pathlib.Path) -> pathlib.Path:
    return vault / TREEBEARD_DIRNAME


def archive_dir(vault: pathlib.Path) -> pathlib.Path:
    return treebeard_dir(vault) / ARCHIVE_DIRNAME


def conversations_dir(vault: pathlib.Path) -> pathlib.Path:
    return treebeard_dir(vault) / CONVERSATIONS_DIRNAME


# Vault-relative form used by guard logic that compares string-form
# paths against archive membership (see `treebeard.chat._path_targets_archive`).
# `PurePosixPath` so the displayed form stays `.treebeard/archive` regardless
# of host OS.
ARCHIVE_REL = pathlib.PurePosixPath(TREEBEARD_DIRNAME) / ARCHIVE_DIRNAME
