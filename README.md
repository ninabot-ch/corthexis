# hexis

**Semantic memory for coding agents. Point it at a folder of notes; your agent
recalls the right ones at the start of a task, in any language, without you
loading an index into context.**

A note is one markdown file holding one fact. `hexis` embeds them, keeps the
index in sync on a filesystem watch, serves recall over MCP, and — the part that
turns out to matter most — **checks that the memory is still actually there**.

Apache-2.0. Python 3.10+. SQLite. No service to sign up for.

---

## Why this exists

Agents do not fail at memory loudly. They fail by knowing less, and you
attribute that to the model.

This tool exists because a memory layer went down twice in production without
anyone noticing. Once the MCP server got overwritten in a config file while a
different server was being added — nine days of sessions ran with no recall at
all. Once the generated index outgrew the harness line budget, and dozens of
notes became invisible at session start. Both times the session booted
normally. Both times the only symptom was an agent that seemed to have gotten
worse.

So `hexis` ships a `selfcheck` that verifies rather than assumes, and the
failure modes it knows about are written down in `examples/` as notes, in the
same format it indexes.

## Quick start

```bash
git clone https://github.com/ninabot-ch/hexis && cd hexis
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt

export HEXIS_NOTES_DIR=./examples          # or your own notes folder
export HEXIS_DB=~/.local/share/hexis/memory.db

.venv/bin/python -m hexis.index            # embeds; first run downloads the model
```

Register the MCP server with your agent:

```bash
claude mcp add -s user hexis $(pwd)/.venv/bin/python -m hexis.server \
  -e HEXIS_NOTES_DIR=$(pwd)/examples \
  -e HEXIS_DB=$HOME/.local/share/hexis/memory.db
```

Then ask it something the notes cover, in whatever language you like — *« mon
agent a répondu 202, c'est bon ? »* matches an English note about HTTP 202,
because the default model is cross-lingual.

Two tools are exposed: `memory_search(query, top_k)` returns ranked notes with a
score and an excerpt; `memory_get(name)` returns one note in full.

To point it at [Claude Code](https://claude.com/claude-code)'s own memory
directory, set `HEXIS_NOTES_DIR` to
`~/.claude/projects/<project-slug>/memory`.

## Keeping it in sync

`systemd/` has units for both halves: a `.path` unit reindexes within seconds of
any note changing (inotify, incremental), and a `.timer` does a full pass
nightly. A second timer runs the health check:

```bash
.venv/bin/python -m hexis.selfcheck
```

It verifies the MCP server is declared in *user* scope (otherwise sessions
started outside the project directory boot with no memory at all), that the
server starts and serves its tools, that the generated index fits the harness
budget in both lines and bytes, that every note on disk appears in it, and that
no note has lost its description to a broken frontmatter block.

Exit 0 and silent when healthy. With `--alert`, the report is piped into
`$HEXIS_ALERT_CMD` — any shell command, so your paging credentials stay out of
this repo.

## Embedding backends

Set `HEXIS_EMBED_BACKEND`:

| | |
|---|---|
| `local` (default) | `sentence-transformers` in-process. No infrastructure, offline after first download. |
| `http` | `POST {HEXIS_EMBED_URL}/api/v1/embed/text` — for when the model lives on a GPU box. |
| `openai` | Any OpenAI-compatible `/embeddings` endpoint. Reads `HEXIS_EMBED_API_KEY`. |

The default model is multilingual on purpose. Changing `HEXIS_EMBED_MODEL` means
re-running with `--rebuild`: vectors from two models are not comparable.

## The note format

See **[SCHEMA.md](SCHEMA.md)**. The short version: one fact per file, a
one-line `description` that doubles as the index hook *and* as embedded
keywords, and a `type` of `user`, `feedback`, `project` or `reference`.

`feedback` and `user` notes are pinned to the top tier of the generated index,
above the recency window — those are the working rules, and a rule you have to
know to search for has already failed.

## About the examples, and about the corpus

`examples/` contains eight real notes, written for this repository. They are
genuine engineering facts — the HTTP 202 trap, the browser profile lock, the
YAML colon that silently kills recall — chosen because they are useful to
anyone and specific to no one.

**They are not a sample of our corpus, and there is no seeded demo data here.**
The corpus this tool was built for is private: five months, 289 notes, 260,000
words, produced while actually operating a company. Roughly every one of those
notes contains either infrastructure detail or commercial information, so none
of it ships, and a redacted version would leak by its shape alone.

We would rather say that plainly than dress up generated data as a working
system. The engine is real and it is all here. The proof you should care about
is not our notes — it is what happens when you run it against yours.

---

Built by [Ninabot Sàrl](https://ninabot.ch), Geneva.
The method around it: **[runhexis.com](https://runhexis.com)**.
