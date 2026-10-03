# Upgrading from CortHeXis 1.x

CortHeXis 1.x kept its index in a SQLite file next to the notes (`CORTHEXIS_DB`, default
`~/.local/share/corthexis/memory.db`), embedded with MiniLM, and was started by the agent
over stdio (`python -m corthexis.server`). 2.0 keeps the index in Postgres + pgvector,
embeds with EmbeddingGemma (or the MIT fallback) through a model server, and runs as one
service. **The notes do not change** — they stay the source of truth.

`corthexis migrate` moves an install over without loss. Every step is idempotent and
resumable (`state.json` in the work folder); nothing is switched until every check passed:

| Step | What it does |
|---|---|
| `archive` | a `tar.gz` of the whole notes folder + a manifest (SHA-256, mtime and declared date of every file), written **before** anything is changed |
| `plan` | the naming-convention plan (renames to `name_with_underscores.md`, YAML repairs), kept as `normalize-plan.txt` |
| `normalize` | the plan applied (`--policy auto`, default), or only after `corthexis migrate approve normalize` (`--policy ask`), or skipped (`--policy off`); the mtimes of rewritten files are put back |
| `dates-files` | no declared date changed, no file newer than in the archive |
| `seed` | the date history: `metadata.modified` first, else the 1.x file date (marked `migrated-mtime`, an approximation) |
| `index` | the first index generation, built with the new model (resumes where it stopped if the model server goes away) |
| `verify` | every 1.x note is in the new index, every date identical, none younger than the migration |
| `switch` | the generation is activated: searches read the new index |

A failed check stops before the switch (status `blocked`, the reason in `corthexis migrate
status`). Read it; `corthexis migrate approve override` goes on anyway, on purpose.
`memory.db` is opened read-only and never written.

## With Docker

Run the migration **before** the first `docker compose up` of the service: a service that
starts first indexes the folder as a new install, and the 1.x file dates are lost.

```bash
git clone https://github.com/ninabot-ch/corthexis corthexis-2 && cd corthexis-2
./setup.sh ~/notes                      # the folder 1.x indexed (CORTHEXIS_NOTES_DIR)
docker compose up -d db corthexis-embed
docker compose run --rm -v ~/.local/share/corthexis:/legacy:ro corthexis \
  corthexis migrate run --memory-dir /notes --work-dir /data/migration \
  --legacy-db /legacy/memory.db
docker compose up -d
```

`migrate run` prints the status; run it again after a `waiting` (model server not ready yet)
or after an `approve`. The log of every step is in `/data/migration/state.json`.

## Without Docker

```bash
pip install "git+https://github.com/ninabot-ch/corthexis"
export CORTHEXIS_DATABASE_URL=postgresql://…  CORTHEXIS_EMBED_URLS=http://127.0.0.1:8080
corthexis migrate run --memory-dir ~/notes --work-dir ~/.local/share/corthexis/migration \
  --legacy-db ~/.local/share/corthexis/memory.db
```

1.x variable names still read by 2.0: `CORTHEXIS_NOTES_DIR` (= `CORTHEXIS_MEMORY_DIR`).
`CORTHEXIS_DB`, `CORTHEXIS_EMBED_BACKEND`, `CORTHEXIS_EMBED_URL` and `CORTHEXIS_ALERT_CMD`
are gone (the store is Postgres; models are served by llama.cpp; alerts go through
`CORTHEXIS_NOTIFY_WEBHOOKS` / `CORTHEXIS_NOTIFY_CMD`).

## After the switch

1. Replace the agent's MCP server (1.x was a stdio command with the SQLite path):
   ```bash
   claude mcp remove -s user corthexis
   claude mcp add -s user --transport http corthexis http://localhost:8420/mcp \
     --header "Authorization: Bearer $(sed -n 's/^CORTHEXIS_TOKEN=//p' .env)"
   ```
2. Install the per-turn recall (new in 2.0): `python3 corthexis/hook.py install`.
3. Remove the 1.x units: `systemctl --user disable --now corthexis-index.path
   corthexis-index.timer corthexis-selfcheck.timer` — the 2.0 service indexes on file
   changes and reviews every hour by itself.
4. Check: `docker compose exec corthexis corthexis status` and the Health tab.

## Rolling back

`memory.db` is untouched: the 1.x checkout with its MCP registration works as before. If the
normalize step renamed files, restore them from the archive (`corpus-2x-*.tar.gz` in the
work folder) — the plan of what was renamed is in `normalize-applied.txt`.

## Tested

`tests/test_memcore_migrate.py::test_migration_from_corthexis_1` builds a 1.x `memory.db`
with the exact 1.x schema and migrates it (names, dates, conventions, untouched index).
End to end, on 03.10.2026: the 1.x examples indexed by 1.x (MiniLM, kebab file names),
then migrated by the 2.0 image into a fresh Postgres with EmbeddingGemma — 8 notes before,
8 after, 8 dates checked, 0 wrong; the files renamed to the convention; a French question
("mon agent a répondu 202, c'est bon ?") finds the English note first.
