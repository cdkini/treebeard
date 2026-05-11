"""Slash commands: `/exit`, `/draft`, and the dispatch table.

The REPL loop in `session.py` looks up the user's input in
`SLASH_HANDLERS` and invokes the matching coroutine. Each handler
returns a `SlashOutcome` telling the loop whether to continue prompting
or break out of the session.

The `/draft` handler is the heaviest: it injects a synthesis instruction
as a user turn, parses the model's reply for a fenced `draft-note`
block, and hands the parsed (title, body) to `commands.note.create_named_note`.
"""

from __future__ import annotations

import pathlib
import re
from collections.abc import Awaitable, Callable
from enum import StrEnum

from claude_agent_sdk import ClaudeSDKClient
from rich.console import Console
from rich.panel import Panel
from rich.text import Text

from treebeard import timefmt, ui
from treebeard.chat.transcript import append_jsonl
from treebeard.ui import status_console


class SlashOutcome(StrEnum):
    """What the REPL should do after a slash handler returns."""

    CONTINUE = "continue"
    BREAK = "break"


SlashHandler = Callable[
    [ClaudeSDKClient, pathlib.Path, pathlib.Path, Console],
    Awaitable[SlashOutcome],
]


# Slash commands surfaced in the header. Each entry is `(command, blurb)`;
# the rendered list is alphabetized by command. Keep this in sync with
# `SLASH_HANDLERS` (slash forms) when a new REPL command is added.
SLASH_COMMANDS: tuple[tuple[str, str], ...] = (
    ("/draft", "synthesize the conversation into a note, then exit"),
    ("/exit", "end the session"),
)


# Synthesis instruction injected as a plain user turn when the user
# types `/draft`. The model already has the conversation history; this
# prompt tells it to stop conversing and emit a structured draft block.
#
# The output uses a fenced code block with the language tag `draft-note`.
# This matters: bare sentinels like `<<<TITLE>>>` get treated as
# decorative markdown by some models and reproduced as-is in prose. A
# real fence is unambiguously a literal block — much harder to confuse.
# Inside the fence: a `title:` line, a `---` divider, then the markdown
# body.
SYNTHESIS_INSTRUCTION = """\
Synthesize the conversation we just had into a draft note.

Your entire reply must be ONE fenced code block tagged `draft-note`,
with no preamble before the opening fence and no text after the closing
fence. Inside the fence: a `title:` line, then a `---` divider on its
own line, then the markdown body.

Worked example (this is the literal shape, not the content to use):

```draft-note
title: Migration Cutover Notes
---
## Risks

Downtime during the cutover window is the dominant risk; rollback is
solid but rehearsed only twice.

## Mitigation

A staged cutover with feature flags reduces blast radius.
```

Title rules:
- Plain prose. No quotes, no trailing period. Slug-friendly.

Body rules:
- DO NOT include YAML frontmatter (`---title: ...---`) — that is added
  separately by the runtime.
- DO NOT include a leading H1 with the title — the title goes on the
  `title:` line, not in the body.
- Use H2/H3 (##/###) for internal structure.
- Voice: neutral / observational. Not first-person, not addressing me.
  Write about the topic, not to me.
- Length should match what we actually covered. A stub for thin chats,
  fuller prose for rich ones. Do not pad.
- If you read or cited any vault notes during the chat, end with a
  Refs: block per the vault CLAUDE.md conventions, but format each
  entry as an Obsidian wikilink: `[[slug|display name]]`.

If the conversation has barely any substance, emit a brief honest stub
anyway — never refuse, never ask follow-up questions.
"""


# Re-prompt sent after a parse failure. Single retry; if the second
# response is also malformed, we abort. Includes a literal worked
# example so the model can pattern-match instead of paraphrasing the
# rules from the first instruction.
SYNTHESIS_REPROMPT = """\
Your previous reply did not parse. Re-emit ONLY a fenced code block
tagged `draft-note`, exactly like this shape:

```draft-note
title: A Short Plain-Prose Title
---
## A heading

Body text here.
```

No preamble before the opening ```draft-note fence, no text after the
closing ``` fence, no commentary inside the block other than the
title line, the `---` divider, and the markdown body.
"""


# Matches the synthesis fence. The `re.DOTALL` flag lets `.*?` cross
# newlines; non-greedy match stops at the first closing ``` so a body
# that itself contains code fences would terminate parsing early — we
# accept that limitation since the body is markdown for a personal
# note, not a tutorial about fenced blocks.
_DRAFT_BLOCK_RE = re.compile(
    r"```draft-note\s*\n"
    r"title:\s*(?P<title>[^\n]+)\n"
    r"---\s*\n"
    r"(?P<body>.*?)\n?"
    r"```",
    re.DOTALL,
)


def _parse_draft_block(text: str) -> tuple[str, str] | None:
    """Pull `(title, body)` out of a fenced `draft-note` reply.

    Returns None when the fence is missing or either piece is empty —
    callers re-prompt once and abort on second failure.
    """
    match = _DRAFT_BLOCK_RE.search(text)
    if match is None:
        return None
    title = match.group("title").strip()
    body = match.group("body").strip()
    if not title or not body:
        return None
    # Trailing newline so the body, when serialized after frontmatter,
    # ends with a single \n the way other notes do.
    return title, body + "\n"


def _render_draft_parse_failure(reply: str, transcript: pathlib.Path) -> None:
    """Surface a parse failure as a Rich panel after the Live region tears
    down. The synthesis turn streams via `Live`, which on some terminals
    re-paints the whole reply per token — a single `ui.error` line gets
    buried in the noise. A panel printed to `status_console` lands cleanly
    below the stream and gives the user something actionable: what the
    model actually emitted (truncated) and where the audit trail lives.
    """
    snippet_limit = 500
    snippet = reply.strip()
    if len(snippet) > snippet_limit:
        snippet = snippet[:snippet_limit] + "\n…[truncated]"
    if not snippet:
        snippet = "(no content)"

    body = Text.assemble(
        ("the model did not emit a parseable `draft-note` fence. ", "white"),
        ("no note was written.\n\n", "white"),
        ("transcript ", "dim"),
        (f"{transcript}\n\n", "white"),
        ("model reply (truncated):\n", "dim"),
        (snippet, "white"),
    )
    status_console.print(
        Panel(
            body,
            title="[bold]could not parse draft[/bold]",
            border_style="red",
            expand=False,
        )
    )


def _unique_path(vault: pathlib.Path, slug: str) -> pathlib.Path:
    """Pick a non-colliding `vault/{slug}.md`, suffixing `-1`, `-2` etc.

    Collision policy is a chat-layer choice (don't clobber existing
    notes), distinct from `post_edit.slugify` which only knows about
    title→slug. Lives here so it stays close to its single caller.
    """
    base = vault / f"{slug}.md"
    if not base.exists():
        return base
    i = 1
    while True:
        candidate = vault / f"{slug}-{i}.md"
        if not candidate.exists():
            return candidate
        i += 1


async def _handle_exit(
    _client: ClaudeSDKClient,
    _vault: pathlib.Path,
    _transcript: pathlib.Path,
    _out: Console,
) -> SlashOutcome:
    return SlashOutcome.BREAK


async def _handle_draft(
    client: ClaudeSDKClient,
    vault: pathlib.Path,
    transcript: pathlib.Path,
    out: Console,
) -> SlashOutcome:
    """Synthesize the conversation into a note, then end the session.

    Injects `SYNTHESIS_INSTRUCTION` as a user turn (the model already
    has the chat history; this just tells it what to do with it),
    parses the sentinel block, lands a `source: [user, llm]` note via
    `create_named_note`, and returns BREAK so the REPL exits.

    On parse failure: one re-prompt. If the second response is also
    malformed, write an error pointing at the transcript and exit
    without creating a note. The transcript is the audit trail.
    """
    # Local imports avoid a load-time cycle: `commands.note` imports
    # `treebeard.config`, fine at runtime but cyclic if pulled in at
    # chat-package import. `session._run_turn` is imported here rather
    # than at module top to break a slash→session cycle.
    from treebeard.chat.session import _run_turn
    from treebeard.commands.note import create_named_note
    from treebeard.config import load_config
    from treebeard.frontmatter import Source
    from treebeard.post_edit import PostEditAbort, scratch_filename, slugify

    append_jsonl(
        transcript,
        {
            "ts": timefmt.now_utc().isoformat(),
            "role": "user",
            "content": SYNTHESIS_INSTRUCTION,
            "meta": {"synthetic": True},
        },
    )

    reply = await _run_turn(client, SYNTHESIS_INSTRUCTION, out, transcript)
    parsed = _parse_draft_block(reply)
    if parsed is None:
        append_jsonl(
            transcript,
            {
                "ts": timefmt.now_utc().isoformat(),
                "role": "user",
                "content": SYNTHESIS_REPROMPT,
                "meta": {"synthetic": True},
            },
        )
        reply = await _run_turn(client, SYNTHESIS_REPROMPT, out, transcript)
        parsed = _parse_draft_block(reply)

    if parsed is None:
        _render_draft_parse_failure(reply, transcript)
        return SlashOutcome.BREAK

    title, body = parsed
    try:
        slug = slugify(title)
    except PostEditAbort:
        slug = scratch_filename(timefmt.now_utc()).removesuffix(".md")
        ui.warn(f"could not slugify title; falling back to {slug}")

    path = _unique_path(vault, slug)
    cfg = load_config()
    create_named_note(
        vault,
        path.stem,
        title,
        timefmt.now_utc(),
        cfg.editor,
        body=body,
        keep_when_unchanged=True,
        sources=[Source.USER, Source.LLM],
    )

    append_jsonl(
        transcript,
        {
            "ts": timefmt.now_utc().isoformat(),
            "role": "system",
            "event": "draft_written",
            "path": str(path),
        },
    )
    return SlashOutcome.BREAK


SLASH_HANDLERS: dict[str, SlashHandler] = {
    "/exit": _handle_exit,
    "exit": _handle_exit,
    "/draft": _handle_draft,
}
