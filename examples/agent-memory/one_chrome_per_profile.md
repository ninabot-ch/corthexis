---
name: one-chrome-per-profile
description: A persistent Chrome profile directory is a mutex — exactly one browser process can hold it. Concurrent agent sessions sharing a profile path will hang at startup instead of failing loudly. Always close the context when the browser task ends, and check SingletonLock for the owning PID.
metadata:
  type: reference
  modified: 2026-09-16
---

A persistent browser profile on disk is a lock, not a config folder. A second
Chrome told to open the same `--user-data-dir` does not error out — it waits.
For an agent, that reads as "the browser tool is slow today", and sibling
sessions silently pile up behind it.

**How to apply:**

- Close the browser context as soon as the browser task is finished, in a
  `finally` — not at the end of the run, and not when convenient.
- When a session does hang on startup, read the lock before killing anything:
  `ls -l <profile>/SingletonLock` resolves to `<host>-<pid>`. If that PID is
  gone, the lock is stale and the symlink can be removed.
- If two workflows genuinely need a browser at once, give them separate profile
  directories. They are cheap; the shared one is not.
