---
name: dates-live-in-the-frontmatter
description: File mtime is not a note's age — any bulk rewrite resets the whole corpus to one timestamp and every note then reads as fresh. Keep the real last-updated date in metadata.modified so search results can be trusted about recency.
metadata:
  type: reference
  modified: 2026-09-16
---

A note's age is part of its meaning. "The primary model is X" written four
months ago and the same sentence written yesterday call for different levels of
trust, and an agent that cannot tell them apart will confidently act on stale
facts.

File mtime looks like the obvious source of that age. It is not: any script that
normalizes, reformats or migrates the corpus rewrites every file, and the whole
memory instantly reads as having been updated this second.

**How to apply:** carry the date in the note itself, as
`metadata.modified: YYYY-MM-DD`, and update it when the *content* changes — not
when the file is touched. Search results then report a real age.

Two guards are worth having, and `corthexis.selfcheck` implements both:

- notes with no resolvable date at all, past a small tolerance for pure
  reference material;
- **every note sharing one mtime**, which is the signature of a bulk rewrite
  having just run over the corpus.

Convert relative dates when writing. "Last week" is unreadable six months later.
