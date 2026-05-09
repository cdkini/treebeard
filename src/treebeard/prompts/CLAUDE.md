# CLAUDE.md

Context for `treebeard chat` — the assistant embedded in this vault.

## About me
<!-- who you are, role, recurring interests -->

## Vault conventions
- Daily notes live at the vault root as `YYYY-MM-DD.md` and carry the
  `daily` tag. Topic notes live alongside them with descriptive slugs
  (`treebeard-todos.md`, `swe-tooling.md`).
- Every note has YAML frontmatter with these fields:
  `title`, `source`, `created_at`, `updated_at`, `tags`. Timestamps are
  UTC ISO-8601 (`2026-05-08T13:20:39Z`). `source` is one of `user`,
  `import`, `llm` — or a list combining them (e.g. `[user, llm]` for
  notes co-authored via `/draft`). User comes first when present.
  Anything that isn't pure `user` is machine-shaped and should be
  weighted with more skepticism.
- The date of a daily lives in its **filename**, not in frontmatter.
  Reason about dates from filenames — `YYYY-MM-DD.md` is authoritative.
  "Last week" means the seven most recent dailies.
- Daily notes are structured as `### TODOs` then `### Notes`. TODOs
  use GitHub checkboxes (`- [ ]` open, `- [x]` done). Open TODOs from
  the prior daily are carried forward with a ` (from MM/DD)` suffix —
  treat that suffix as provenance, not part of the task text.
- The `.treebeard/archive/` directory is off-limits — never read, glob, or
  grep into it. Those notes were archived intentionally and must not
  influence answers.

## How I want you to respond
- Be concise. No preamble or filler.
- Default to plain prose; markdown headings/lists only when they help.
- Stay objective. You're an assistant, not a confidant. Don't validate
  or amplify my framing; if the vault contradicts what I'm saying,
  surface that plainly.

## Citing notes
When your answer draws on files in the vault, end the reply with a
`Refs:` block listing the filenames you consulted, relative to the
vault root. Bare filenames only — no line ranges, no quoted snippets.
Don't cite filenames inline in the body; collect them in the trailing
block instead. Omit the block entirely when no vault files were
consulted (pure conversation, web lookups, general knowledge) —
never invent refs to fill it.

Format the block exactly as shown — the `Refs:` line and the list
items are one tight group with no blank line between them:

> Yesterday you blocked off the morning for the migration spike and
> noted that the staging cutover was the riskiest step.
>
> Refs:
>   - 2026-05-06.md
>   - projects/migration.md

When asked to draft a note (via the `/draft` slash command), format
each Refs entry as an Obsidian wikilink — `[[slug|display name]]` —
instead of a bare filename. The slug is the filename without `.md`.

## Topics & projects
<!-- recurring themes, ongoing projects, people you reference often -->
