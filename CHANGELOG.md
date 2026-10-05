# Changelog

## 2.0.0 — 2026-10-05

CortHeXis becomes a product of its own: the memory engine of SOKKAN 3.0, usable alone.

### Engine
- **Postgres + pgvector** store instead of SQLite: notes, version history, passages in
  index **generations** (a model change builds a new generation next to the active one),
  links, recall log, review history, bench.
- **Hybrid search**: dense (HNSW, exact scan on small corpora) + IDF-weighted keywords on
  the head and the body, linear blend tuned per model (RRF optional), optional reranker.
  Results carry their age and the provenance of the date.
- **Embeddings by llama.cpp**: EmbeddingGemma-300m (Q8) by default, downloaded at first run
  after the Gemma Terms of Use are accepted, with **multilingual-e5-base (MIT)** as the
  fallback; pinned files checked against their SHA-256; chain of servers (GPU then CPU)
  with dimension checks. Profiles `leger`, `standard`, `gpu`. Bench: MRR 0.82 (Gemma, CPU),
  0.88 (GPU with reranker), against 0.55 for the 1.x MiniLM.
- **Indexer**: incremental, watch + periodic, naming convention kept by lossless repairs,
  link-insensitive dates with provenance, `MEMORY.md` within the session's budget.
- **Recall at every turn** (`UserPromptSubmit`) and for sub-agents (`PreToolUse` on
  `Task|Agent`), threshold per model, deduplicated per session, logged.
- **Review**: chain checks (MCP handshake, model servers, indexer), structure, graph,
  dates, drift (dormant projects, description ≠ body, near duplicates), security
  (secrets, injected orders), bench regressions; score, history, time to fix, alert policy.
- **Repairs** as proposals with a diff (relink, merge, rename, close), applied
  all-or-nothing after approval, with a backup.
- **Recall bench** on your own notes (harvested from transcripts, written, generated),
  with regressions as review findings; gated profile/model **switch** with rollback.

### Service
- `corthexis serve`: indexer + reviewer + dashboard + **MCP over HTTP** (`/mcp`, Bearer)
  in one process; `corthexis mcp` for stdio.
- **Dashboard** open-sourced (was private): graph, recall, health, repairs with approval,
  bench, pulse; English and French; demo mode.
- **Recall hook** for Claude Code with `install` / `uninstall` (standard library only).
- **Notifications** by webhooks (JSON, Slack, Discord, ntfy, Mattermost, Teams) or a command.
- `docker compose`: `db`, `corthexis-embed-fetch` (licence + download), `corthexis-embed`,
  `corthexis-rerank`, `corthexis`; GPU overrides (Intel SYCL, NVIDIA CUDA); `setup.sh`.
- CLI: `index`, `search`, `get`, `status`, `review`, `eval`, `normalize`, `migrate`,
  `models`, `profile`, `hook`, `notify-test`.

### Migration
- `corthexis migrate` from a 1.x (or SOKKAN 2.x) SQLite index — archive, plan, normalize,
  dates, index, verify, switch; resumable; see docs/MIGRATION.md.
- `CORTHEXIS_NOTES_DIR` is still read; `CORTHEXIS_DB`, `CORTHEXIS_EMBED_BACKEND`,
  `CORTHEXIS_EMBED_URL`, `CORTHEXIS_ALERT_CMD` are gone. `selfcheck` became `review --chain`.
- Examples moved to `examples/agent-memory/` (file names in the convention) and the
  fictional demo corpus `examples/mirabeau-conseil/` added.

### Bench
- **Public recall bench** (`bench/`): 60 questions in French, English and German over the
  demo corpus, `bench/demo/bench.py` to rerun it, results for four model setups, and the
  method of the private 300-question reference bench. `corthexis eval import FILE.jsonl`
  loads a written bank.

### Fixed
- The "whole memory rewritten at once" warning fired on any fresh clone or backup restore:
  it now looks only at notes whose age comes from the file.
- Recall: one word of a note's name in the message ("deploy", "mission") forced that note
  into the context; it now takes the full name, or two segments of it as whole words.
- Review: an Exoscale key identifier alone (the public half of the key) is no longer reported
  as a secret; it is when its secret sits in the same note.
- Normalize: renaming a note to the convention re-attaches the links written with its old
  name or a slug variant of it (`[[Team Calendar]]`, `[[team_calendar]]`).
- Migration: a note owned by another user keeps its date instead of failing the step.

## 1.x — 2026-09-16

First public version (unversioned): SQLite index, MiniLM embeddings (local, HTTP or OpenAI-compatible),
MCP stdio server, filesystem-watch reindexing, `selfcheck`.
