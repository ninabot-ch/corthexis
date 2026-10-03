---
name: verify-against-an-independent-witness
description: The core working rule — never report an action as done on the strength of the same system that performed it. Confirm against a witness that system does not control, and state plainly what was actually observed versus what was assumed.
metadata:
  type: feedback
  modified: 2026-09-16
---

An agent that reports what it *intended* rather than what it *observed* is worse
than one that reports nothing, because the error is now in your notes and will
be recalled as fact.

**Why:** every system that performs an action also reports on it, and those two
roles conflict. An API returns 2xx for "I accepted your request". A deploy
script exits 0 when it finished running, not when the service came back. A test
suite passes when no assertion failed, which is not the same as the feature
working.

**How to apply:** before writing "done", name the witness.

| Action | Not evidence | Evidence |
|---|---|---|
| Async write | the 2xx that accepted it | the downstream system now holds the record |
| Deploy | the script exited 0 | the service answers on its port, new version string |
| Purchase | the order was accepted | the balance moved by the exact amount |
| Fix | the code changed | the failing case now passes |

When no independent witness exists, say so in the same sentence as the claim.
"Registered" and "the API accepted the registration, not yet visible at the
registry" are different facts, and only one of them is safe to act on later.

See [[http-202-is-not-success]], [[a-correction-becomes-a-rule]].
