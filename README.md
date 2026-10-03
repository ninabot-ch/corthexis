# CortHeXis

**A memory for AI agents that reads itself back.**

Your agents forget between sessions, and they do not fail loudly when they do: they just
know less, and you blame the model. CortHeXis keeps a folder of short notes — one fact
each, plain markdown you own — and puts the right ones in front of the agent **at every
message**, in any language. Then it does the part nobody does: every hour it **re-reads
the whole memory** and tells you what went wrong — a server no session can reach, links
to notes that no longer exist, a fact corrected in one place and not the other, a project
still "in progress" two months later, a key pasted into a note — and proposes the repair,
which is only written once you approve it.

- **Recall at every turn.** A hook adds the relevant notes to each message, and to every
  sub-agent's prompt (a sub-agent otherwise starts with no memory at all). Over MCP, agents
  can also search (`memory_search`) and read (`memory_get`) on their own.
- **Hybrid, cross-lingual search.** Meaning and keywords together; a French question finds
  an English note. Every result carries its age and where that date comes from.
- **A memory that checks itself.** Health score, findings with their remedy, history,
  daily digest to your webhook, repairs with a diff and an approval.
- **Measured, on your notes.** A recall bench built from your own questions and sessions,
  with every run kept and regressions flagged.
- **Yours.** Apache-2.0, self-hosted, no account. The notes are files; the index is
  derived from them and can be rebuilt at any time.

Built by [Ninabot](https://ninabot.ch) · [corthexis.com](https://corthexis.com) ·
live demo on a fictional corpus: [demo.corthexis.com](https://demo.corthexis.com)

![The CortHeXis dashboard: the graph of the notes, recall, health](docs/img/dashboard.jpg)

---

## Quick start

Docker with Compose v2, ~2 GB of RAM, 4 cores.

```bash
git clone https://github.com/ninabot-ch/corthexis && cd corthexis
./setup.sh                 # token, your notes folder, the model licence question
docker compose up -d       # → http://localhost:8420
```

`./setup.sh` writes `.env` (a random `CORTHEXIS_TOKEN`, the notes folder, your uid), builds
the image and asks which embedding model to use (see [Licences](#licences)). Without an
argument it points at `examples/agent-memory`; give it your folder:
`./setup.sh ~/.claude/projects/<project-slug>/memory` (where Claude Code keeps its memory)
or any folder of markdown notes. Unattended: `CORTHEXIS_ACCEPT_GEMMA_TERMS=1|0 ./setup.sh DIR`.

Open http://localhost:8420 and paste the token (`grep CORTHEXIS_TOKEN .env`). The first
indexing takes a few seconds per note on a CPU; the graph fills as it goes.

## Connect your agent

**MCP** (any MCP client; Claude Code shown):

```bash
claude mcp add -s user --transport http corthexis http://localhost:8420/mcp \
  --header "Authorization: Bearer $(sed -n 's/^CORTHEXIS_TOKEN=//p' .env)"
```

`-s user` matters: a server declared in one project only is missing from every session
started elsewhere — the most common way a memory silently disappears.

**Recall at every message** (Claude Code hooks, standard library only, nothing to install):

```bash
python3 corthexis/hook.py install            # ~/.claude/settings.json — every session
python3 corthexis/hook.py install --scope local --project-dir ~/my-project   # one project
python3 corthexis/hook.py uninstall          # removes only its own entries
```

It installs a `UserPromptSubmit` hook and a `PreToolUse` hook on `Task|Agent`, reads the
token from `./.env` and keeps it in `~/.config/corthexis/` (mode 600). The block it adds is
framed as data, not instructions — notes are written by agents, and a note may carry an
order. Notes already given in a session are not given again.

**Without HTTP**: `pip install .` then `claude mcp add -s user corthexis -- corthexis mcp`
with `CORTHEXIS_DATABASE_URL` and `CORTHEXIS_EMBED_URLS` in its environment (stdio).

## The dashboard

| | |
|---|---|
| **Graph** | notes as neurons, `[[links]]` as synapses, broken links as red ghosts. Modes: *Synapses* (links), *Constellation* (position = meaning), *Age*, *Health* (only flagged notes lit). Live: a note written by an agent flashes. |
| **Recall** | the same search as the agents', with scores and excerpts. |
| **Health** | score 0-100 and its history, findings grouped by severity with the remedy and the notes concerned, open problems and mean time to fix. |
| **Repairs** | relink, merge, rename, close a dormant project: each is a proposal with the full diff; approve to write (a copy of what is overwritten is kept, and a note edited in between makes the repair refuse itself). |
| **Bench** | hit@1, hit@5, MRR on your own questions; add questions, run it, see regressions. |
| **Pulse** | freshness, types, families, the model and servers in use, the licence. |

English and French (`?lang=fr`, `CORTHEXIS_UI_LANG`). Reading needs the token unless
`CORTHEXIS_DASHBOARD_PUBLIC=1`; writing always does. The port is bound to loopback: to
share it, put your own access proxy in front.

## What the review checks

Each check is a failure that happened in production before it was written down:

| Theme | Checks |
|---|---|
| chain | the MCP server answers when started the way a session starts it; embedding and reranking servers up; the indexer keeps up; the index is in sync with the files |
| structure | unreadable header, no description, no type, file outside the naming convention, very long notes |
| graph | `[[links]]` to missing notes (a replacement is suggested from renames), isolated notes |
| dates | notes without a date, reconstructed dates, a whole folder rewritten at once |
| drift | dormant projects still open, a description corrected but not the body, near duplicates |
| security | key-shaped strings (kind and line only, never the value), text giving orders to the agent |
| recall | regressions of the bench |

Notifications: `CORTHEXIS_NOTIFY_WEBHOOKS` (JSON, or `slack+`, `discord+`, `ntfy+`,
`mattermost+`, `teams+` prefixed URLs) and/or `CORTHEXIS_NOTIFY_CMD` (a command fed the
message). One digest a day when the findings changed (`CORTHEXIS_REVIEW_DIGEST_AT`), at once
on a new critical problem. Test: `docker compose exec corthexis corthexis notify-test`.

From the sessions' side (is the server declared in the agent's configuration, is the hook
installed?) run `corthexis review --chain` on the agent's machine — `systemd/` has a timer.

## Profiles

| Profile | Embedding | Reranker | For | Query p50 | MRR |
|---|---|---|---|---|---|
| `leger` (default) | EmbeddingGemma-300m Q8, CPU | — | 4 cores, 4 GB | 53 ms | 0.82 |
| `standard` | same, CPU | bge-reranker-v2-m3, background only | ≥ 8 cores, ≥ 16 GB | 35 ms | 0.82 (0.84 reranked) |
| `gpu` | same, GPU | Qwen3-Reranker-0.6B, every search | a GPU with ≥ 4 GB | 13 ms | 0.88 |

Figures: memory bench of 03.10.2026 (300 questions in French, English and German over a real
corpus, hybrid search; GPU pass on an Intel Arc Pro B60). On the same bench the 1.x model,
MiniLM, scored 0.55 and the MIT fallback, multilingual-e5-base, 0.74.
`docker compose exec corthexis corthexis profile` recommends one for the machine.

```bash
# GPU, Intel Arc (SYCL) or NVIDIA (CUDA, needs the NVIDIA Container Toolkit)
echo CORTHEXIS_MEMORY_PROFILE=gpu >> .env
docker compose -f docker-compose.yml -f docker/compose.gpu-intel.yml --profile rerank up -d
docker compose -f docker-compose.yml -f docker/compose.gpu-nvidia.yml --profile rerank up -d
```

Apple GPU: Docker has no Metal — run `docker/embed/run.sh` natively (`LLAMA_SERVER=llama-server`)
and set `CORTHEXIS_EMBED_URLS`. Several servers of the same model can be listed (GPU then CPU):
a server that fails is skipped for 30 s, and a server answering another dimension is refused
instead of polluting the index. Changing the embedding model builds a new index generation
next to the active one; searches use the old one (keywords only while the model differs) until
the new one is complete.

Docker hosts whose address pools are exhausted: add `-f docker/compose.bridge.yml`.

## Licences

**The code** is Apache-2.0 ([LICENSE](LICENSE)). The dashboard ships d3 (ISC) and marked
(MIT), see [NOTICE](NOTICE). **No model weights are shipped**: they are downloaded on first
run, pinned to a repository commit and checked against their SHA-256.

The default embedding model, **EmbeddingGemma-300m** (Google), is distributed under the
[Gemma Terms of Use](https://ai.google.dev/gemma/terms), which are not an open-source licence
(use restrictions apply). `./setup.sh` shows their summary and asks; the decision is recorded
with the version of the terms, the date and who answered (`licence.json` in the models
volume). If you decline, or do not answer, CortHeXis runs **multilingual-e5-base** (MIT)
instead, with no loss of function — recall is a bit lower. Change your mind later:

```bash
docker compose run --rm corthexis-embed-fetch python /opt/corthexis/models.py setup --accept
docker compose restart corthexis-embed corthexis    # the index is rebuilt with the new model
```

A machine without internet access: copy the GGUF file into the models volume (`gguf/`); it
is used if its hash matches. Mirror: `CORTHEXIS_MODEL_BASE_URL`.

## Upgrading from CortHeXis 1.x

1.x kept a SQLite index next to the notes (MiniLM, `CORTHEXIS_NOTES_DIR`, `CORTHEXIS_DB`).
`corthexis migrate` carries it over without loss — archive first, every note and every date
checked before the switch, the old index left untouched for a rollback. See
[docs/MIGRATION.md](docs/MIGRATION.md).

## SOKKAN

[SOKKAN](https://sokkan.ch) 3.0 embeds this engine; its memory tab is called CortHeXis.
Everything SOKKAN does with the memory — per-turn recall in its chat and terminal sessions,
review, repairs, bench, profile switch — is this code. The `SOKKAN_*` variable names are
read as a fallback of the `CORTHEXIS_*` ones.

## Configuration

Every setting is a `CORTHEXIS_*` variable; [.env.example](.env.example) lists the ones of the
Docker install, [docs/ENGINE.md](docs/ENGINE.md) all of them (store, search, recall, review
thresholds, profiles, models). The command line (`corthexis --help`) is in the image:

```bash
docker compose exec corthexis corthexis status
docker compose exec corthexis corthexis search "how do we deploy?"
docker compose exec corthexis corthexis get deploy-runbook
docker compose exec corthexis corthexis review | head
docker compose exec corthexis corthexis eval add "how do we deploy?" deploy-runbook
```

## About the examples

`examples/agent-memory/` holds eight engineering notes written for this repository — the
HTTP 202 trap, the browser profile lock, the YAML colon that silently kills recall — useful
to anyone and specific to no one. `examples/mirabeau-conseil/` is the corpus of the public
demo: 38 notes of a **fictional** consulting firm, in French, with one broken link left on
purpose so that the review has something real to find.

Neither is a sample of the corpus CortHeXis was built on. That one is private — months of
notes written while actually running a company, infrastructure and commercial detail in
nearly every file — and a redacted copy would leak by its shape alone. The engine is real
and it is all here; the proof that matters is what it does with your notes.

## Development

```bash
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/ruff check corthexis tests && .venv/bin/pytest -q
# the store, recall and dashboard tests need a Postgres with pgvector:
docker run -d --name cx-test-pg -p 127.0.0.1:55432:5432 -e POSTGRES_USER=cx \
  -e POSTGRES_PASSWORD=test pgvector/pgvector:pg16
CORTHEXIS_TEST_PG_DSN=postgresql://cx:test@127.0.0.1:55432/postgres .venv/bin/pytest -q
```

[SCHEMA.md](SCHEMA.md) — the note format · [docs/ENGINE.md](docs/ENGINE.md) — the engine ·
[docs/DEMO.md](docs/DEMO.md) — a public read-only showcase · [CHANGELOG.md](CHANGELOG.md)
