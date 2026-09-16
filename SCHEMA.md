# The note format

A note is one markdown file holding **one fact**. That constraint is doing real
work: notes that hold three facts get recalled for the wrong one, and get
rewritten by whoever needs to change only the second.

```markdown
---
name: http-202-is-not-success
description: A registrar API returned 202 for two registrations; only one reached the registry. 202 means accepted, not done — verify against an independent witness.
metadata:
  type: reference
  modified: 2026-09-16
---

The fact, stated plainly. For `feedback` and `project` notes, follow with
**Why:** and **How to apply:** lines.

Link related notes with [[their-name]].
```

## Fields

| Field | Required | What it does |
|---|---|---|
| `name` | yes | The recall handle. Kebab-case. Defaults to the filename stem. |
| `description` | yes | One line. See below — this is the highest-leverage field in the file. |
| `metadata.type` | yes | `user` · `feedback` · `project` · `reference` |
| `metadata.modified` | recommended | `YYYY-MM-DD`, the real last-updated date. See [[dates-live-in-the-frontmatter]]. |

⚠️ **Quote any `description` containing a colon.** An unquoted colon breaks the
whole YAML block and the note silently loses its name *and* its description.
See `examples/frontmatter-colon-breaks-recall.md`.

## Why `description` carries the weight

It does two jobs at once:

1. it is the hook in the generated index — often the only thing about this note
   that a session sees before deciding whether to open it;
2. it is **prepended to the note's text before embedding**, so its keywords lift
   the recall of every chunk in the file.

A vague description costs you twice. Write the description as if it were the
search query someone would type to find this note — because effectively, it is.

## The four types

**`user`** — who the human is: role, expertise, standing preferences. Rarely
changes, always worth recalling.

**`feedback`** — how you should work. Corrections *and* confirmed approaches.
Always carries its reason: a rule without its why gets misapplied as soon as
circumstances shift. These are pinned to the top of the index.

**`project`** — ongoing work, goals, constraints that are not derivable from the
code or the git history. Convert relative dates to absolute ones when writing.

**`reference`** — pointers to external resources, and durable technical facts
like the ones in `examples/`.

## What not to write down

- what the repository already records — code structure, past fixes, git history
- what only matters inside the current conversation
- anything secret. Notes are plain files on disk and get embedded into a
  database. Treat the corpus as readable by anything that can read the folder.

If someone asks you to remember something in the first two categories, ask what
was non-obvious about it and write *that* down instead.

## Maintenance

Before adding a note, look for one that already covers the ground. Update that
file rather than creating a near-duplicate — two notes on one subject means
search returns the stale one half the time. Delete notes that turn out to be
wrong; a corrected memory is worth more than a complete one.
