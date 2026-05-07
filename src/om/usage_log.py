"""Append-only invocation log written to `<vault>/.om/usage.log`.

One line per `om` invocation, formatted as
`<UTC ISO 8601 timestamp> | <argv joined by spaces>`. Writes are
silently best-effort: any failure (no vault configured, vault missing,
disk full, permission denied) is swallowed so logging never breaks
the user's command.
"""

from __future__ import annotations

from datetime import UTC, datetime

from om.config import load_vault_path

USAGE_LOG_DIR = ".om"
USAGE_LOG_FILENAME = "usage.log"


def log_invocation(config_dir: str | None, argv: list[str]) -> None:
    try:
        vault = load_vault_path(config_dir)
        if vault is None:
            return
        log_dir = vault / USAGE_LOG_DIR
        if not log_dir.is_dir():
            return
        timestamp = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        line = f"{timestamp} | {' '.join(argv)}\n"
        # POSIX O_APPEND makes single small writes atomic across processes.
        with (log_dir / USAGE_LOG_FILENAME).open("a", encoding="utf-8") as fh:
            fh.write(line)
    except Exception:
        return
