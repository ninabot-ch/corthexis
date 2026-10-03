---
name: http-202-is-not-success
description: A registrar API returned HTTP 202 for two domain registrations in one loop; only one actually reached the registry, and the other was never charged. 202 means "accepted", not "done" — verify against an independent witness (the registry itself, and the account balance) before reporting success.
metadata:
  type: reference
  modified: 2026-09-16
---

Registering two domains through a provider API, in the same loop, both calls
returned `HTTP 202`. Six minutes later: one domain was live at the registry, the
other did not exist anywhere — not at the registry, not on the account, and
never billed. No error was ever returned.

`202 Accepted` is a promise to try, not a receipt. Any async-write API can do
this to you.

**How to apply:** never report a write as done on the strength of a 2xx from the
API that accepted it. Confirm against a witness the API does not control:

- the authoritative registry / downstream system (here: RDAP, `200` = exists)
- a monetary counter (here: the account's annual balance, which moves by the
  exact amount per unit actually processed)

The second witness is what makes a retry safe: if the balance never moved, the
failed unit was never charged, so re-running it cannot double-bill.

See [[verify-against-an-independent-witness]].
