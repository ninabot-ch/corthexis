---
name: frontmatter-colon-breaks-recall
description: An unquoted description containing a colon makes the whole YAML frontmatter block fail to parse, so the note silently loses both its name and its description — degraded recall and a blank index line, with no error anywhere. The indexer falls back to line parsing, but quote the value.
metadata:
  type: reference
  modified: 2026-09-16
---

In YAML, this is not what it looks like:

```yaml
description: Deploy runbook: build, push, restart
```

The second colon makes the mapping ambiguous and the *entire* frontmatter block
fails to load. The note then keeps neither its `name` nor its `description` —
and since the description is prepended to the embedded text, recall for that
note quietly degrades while its index line goes blank.

Nothing errors. The note is still there, still indexed, just much harder to find.

**How to apply:** quote any description containing `:`, `#`, or a leading `-`.
`hexis.index` does fall back to a line-by-line parser when YAML refuses the
block, and warns on stderr, but do not rely on the fallback — it is a net, not a
floor.

`python -m hexis.selfcheck` flags notes with an empty description for this exact
reason.
