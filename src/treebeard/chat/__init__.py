"""`tb chat` — Claude Agent SDK REPL with JSONL transcript.

Public surface is intentionally small: the `chat` command in
`commands.chat` only needs `run_repl`, and `ALLOWED_TOOLS` is exposed
because the read-only-tools invariant is a security-relevant constant
worth asserting externally.

Everything else lives in submodules and is imported directly by tests
that exercise internals (`treebeard.chat.client`, `treebeard.chat.slash`,
`treebeard.chat.prompt`, `treebeard.chat.ui`). Don't widen this list
without a reason.
"""

from treebeard.chat.client import ALLOWED_TOOLS
from treebeard.chat.session import run_repl

__all__ = ("ALLOWED_TOOLS", "run_repl")
