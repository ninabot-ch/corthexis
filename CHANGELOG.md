# Changelog

## 2.1.2 — 2026-10-10

- **The local fastembed model is loaded once per process.** A `LegacyEmbedder` built anew at
  every store re-check reloaded the model (4-13 s on a small host); a recall hook with a 5 s
  budget then timed out and the prompt waited. The model is now cached per (model, cache
  directory), shared by every embedder of the process. Same fix as SOKKAN 3.4.4.

## 2.1.1 — 2026-10-09

- `/api/search` takes `projects=a,b`: one instance can serve several corpora (one per
  character of a game, one per product…) and the caller asks for the ones it needs. The
  scope is narrowed by the service's own (`CORTHEXIS_RECALL_PROJECTS`), never widened; an
  empty or unknown list finds nothing. Each result now carries its `project`.

## 2.1.0 — 2026-10-08 — « Projects and levels »

The engine of SOKKAN 3.2–3.4 (multi-project, classification), usable alone. A standalone
memory does not have to know about any of it: one project, every note at the default level,
every call as in 2.0.

### Projects
- Every note belongs to **one project** (`notes.project`, default `default`); names are
  unique **per project**, so are links and the version history. One notes folder = one
  project (`CORTHEXIS_MEMORY_PROJECT` for the indexer of that folder).
- A **scope** is the set of projects a caller may read (`corthexis.scope`): `None` = no
  scope (everything, as before); a tuple of projects = only their notes; an empty scope =
  nothing. Fail-closed: a note without a project counts as `default`, an invalid project
  name is dropped and never widens the scope.
- The scope is applied **at every stage** of a search (dense, lexical, final guard) and by
  the recall (ranking and quoted names alike: a note of another project "does not exist",
  even quoted by its full name). A shared project `shared` resolves after the caller's own.
- Review: `PgSource(store, project=…)` reads one project — near duplicates and renames
  never pair notes of two projects.

### Levels
- Five levels on a note, stored as a rank: `public` (0) < `team` (1) < `project` (2,
  the default) < `confidential` (3) < `restricted` (4). Frontmatter `classification:`
  takes the id, the rank or your own label (`CORTHEXIS_CLASSIFICATION_LABELS`, five
  labels); a value that is set but not understood is `restricted`, never `public`.
- A scope entry may carry a **clearance**: `radio@3` = the notes of `radio` up to
  `confidential`; a bare `radio` reads up to the default level, so an entry that lost
  its clearance on the way can only see less. Two entries for one project keep the lower.
- An index upsert **never lowers** a level (an edit of the file cannot declassify a
  note); `Store.set_level` does, with a floor per note (`note_level_floor`) that a later
  write cannot go under. `Store.session_level` = the highest level a session obtained.
- **Audited recall**: every injection and every scoped read lands in `note_access` (who,
  via which path, which note, at which level, for which query); `Store.access_log` reads
  it. The MCP `memory_get` logs its reads when the server runs with a scope.

### Service, MCP, hook
- `CORTHEXIS_RECALL_PROJECTS=radio,shared@1` scopes the recall hook, the MCP tools and the
  CLI of that process; `CORTHEXIS_RECALL_REQUIRE_SCOPE=1` makes the hook recall nothing
  without one (what SOKKAN sets). Results carry `project` and `level`.
- `corthexis.service.search / get / links` take `projects=`; `service.get_record` gives the
  record a caller may read.

### Migrations
- `0011_note_project` (project column, existing notes → `default`), `0012_project_names`
  (unique per project; links, history and recall log carry the project), `0013_classification`
  (level, floor, `recall_log.level`, `note_access`). Applied by `Store.migrate()` on first
  open, in place, nothing moved — tested on a frozen 2.0.0 database
  (`tests/fixtures/schema_2_0_0.sql`).
- Compatibility: `Store.get_note(name)` reads the default project; `resolve_note(name,
  scope)` is the scoped lookup. `IndexStore.note_names(generation, project=…)`.

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
