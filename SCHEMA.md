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
| `name` | yes | The recall handle. Kebab-case (`http-202-is-not-success`). Defaults to the file name. |
| `description` | yes | One line. See below — this is the highest-leverage field in the file. |
| `metadata.type` | yes | `user` · `feedback` · `project` · `reference` |
| `metadata.modified` | recommended | `YYYY-MM-DD`, the real last-updated date. See [[dates-live-in-the-frontmatter]]. |
| `priority` | no | `high`: a slight boost in search (opt-in, `CORTHEXIS_PRIORITY_BOOST`). |
| `classification` | no | `public` · `team` · `project` (default) · `confidential` · `restricted`, the rank 0-4, or your own label (`CORTHEXIS_CLASSIFICATION_LABELS`). An unknown value counts as `restricted`. Only read by callers whose scope clears that level — see [Projects and levels](README.md#projects-and-levels). |

## File name

The file of a note named `http-202-is-not-success` is `http_202_is_not_success.md`: the
name with `_` instead of `-`. One convention, so that an agent writing a note always lands
on the same file instead of creating a twin. The indexer brings the folder back to it on
its own (`CORTHEXIS_NORMALIZE`, on by default): a file renamed to its name (and the links
to it rewritten), a header-less file appended to the note it belongs to, a broken YAML block
rewritten, a missing type filled in, a dangling link re-attached when it designates one note
unambiguously — every change lossless. A file touched less than five minutes ago is never
renamed nor merged: a note being written is not moved under the writer's feet. `corthexis normalize --dry-run` shows the plan.
`MEMORY.md` is the generated index (one line per note, within the budget a session loads):
never edit it by hand.

## Links

`[[another-note]]` links two notes (by name; the file name and `_`/`-` variants resolve
too). Links inside code are ignored. The review reports links to notes that do not exist,
suggests the note a renamed link meant, and proposes the fix; the dashboard draws the
links as the graph.

## Dates and where they come from

Every search result carries the note's age **and the provenance of that date**, because
an agent must not read a two-month-old fact as today's:

| Source | Meaning |
|---|---|
| `frontmatter` | `metadata.modified`, written on purpose |
| `indexed` | measured: the first time the indexer saw this content (the header was not bumped) |
| `transcript` | reconstructed from session transcripts — a real write, day exact |
| `migrated-mtime` | the file date imported once from a 1.x / SQLite install — an approximation |
| `inferred` | deduced from the body — an order of magnitude, never a measure |

The date is **link-insensitive**: rewriting `[[links]]` (a rename, a repair) does not
make a note young again.

⚠️ **Quote any `description` containing a colon.** An unquoted colon breaks the
whole YAML block and the note silently loses its name *and* its description.
See `examples/agent-memory/frontmatter_colon_breaks_recall.md`.

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
like the ones in `examples/agent-memory/`.

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
