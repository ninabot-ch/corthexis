---
name: lint-strict-only-in-production-build
description: A lint rule set that is advisory in the dev server and fatal in the production build turns a green local run into a failed deploy. Run the production build locally before pushing, and treat the dev server's silence as no evidence at all.
metadata:
  type: feedback
  modified: 2026-09-16
---

Several frontend toolchains run lint in "warn" mode under the dev server and in
"error" mode during the production build. The consequence is a specific, and
very repeatable, waste of a deploy cycle: everything is green locally, CI fails
on a rule that the dev server never mentioned.

**Why:** the dev server optimizes for iteration speed, the production build for
correctness. They are not the same gate, so passing one is not evidence about
the other.

**How to apply:** before pushing a frontend change, run the *production* build
locally, not the dev server. When a build fails on a lint rule you never saw, do
not reach for an inline disable — the rule was on all along; only the severity
changed.

See [[verify-against-an-independent-witness]].
