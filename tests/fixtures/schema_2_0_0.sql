-- SOKKAN 3.0 memory store — schema version 1 (Postgres 16 + pgvector >= 0.7).
--
-- The .md notes stay the source of truth: notes / chunks / links / lex_df are an index that
-- can be rebuilt from the files. note_versions and recall_log are HISTORY and cannot: back
-- them up with the database.
--
-- Embedding dimension per generation. An index generation = one embedding model (identity +
-- dimension). Two generations of different dimensions must coexist while a new one is
-- built in the background (P0-1). `chunks` is therefore LIST-partitioned by generation:
--   * the parent declares `embedding halfvec` WITHOUT a dimension, so every partition can
--     hold its own dimension, enforced by a CHECK (vector_dims) added on the partition;
--   * each partition gets its own HNSW index on the expression
--     `(embedding::halfvec(<dim>))`, which is how pgvector indexes a dimension-less column;
--   * retiring a generation is DETACH + DROP of its partition: instant, no 250 000-row
--     DELETE, no bloat to vacuum, the other generation's index is never touched.
-- (The alternative, one table + partial expression indexes `WHERE generation_id = n`, works
-- too, but deleting a retired generation then costs a mass DELETE + VACUUM on the table the
-- live search is reading.) Partitions are created by Store.create_generation(), not here.
--
-- Migrations: this file is version 1. Later changes go in migrations/NNNN_<name>.sql
-- (NNNN > 0001), applied in order by Store.migrate() and recorded in schema_migrations.

CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS schema_migrations (
    version     integer PRIMARY KEY,
    name        text NOT NULL,
    applied_at  timestamptz NOT NULL DEFAULT now()
);

-- One embedding model = one generation. At most one is `active` (served by default);
-- `building` is being filled in the background; `retired` stays searchable on demand
-- (rollback window, 7 days by default) until purged.
CREATE TABLE IF NOT EXISTS index_generations (
    id              serial PRIMARY KEY,
    embed_identity  text NOT NULL,
    dim             integer NOT NULL CHECK (dim BETWEEN 1 AND 4000),  -- halfvec HNSW limit
    status          text NOT NULL DEFAULT 'building'
                    CHECK (status IN ('building', 'active', 'retired')),
    created_at      timestamptz NOT NULL DEFAULT now(),
    activated_at    timestamptz,
    retired_at      timestamptz,
    chunk_count     bigint NOT NULL DEFAULT 0,
    hnsw_m          integer NOT NULL DEFAULT 16,
    hnsw_ef_construction integer NOT NULL DEFAULT 64,
    index_built     boolean NOT NULL DEFAULT false,
    -- dense/lexical blend suited to this model (NULL = default of its model family)
    lexical_weight  double precision CHECK (lexical_weight BETWEEN 0 AND 1)
);
CREATE UNIQUE INDEX IF NOT EXISTS index_generations_one_active
    ON index_generations ((true)) WHERE status = 'active';

-- Notes: generation-independent (the text does not depend on the model).
-- head_tokens / lex_tokens are the folded keyword sets of the lexical score
-- (head = name + description ; lex = name + description + body), see search.py.
CREATE TABLE IF NOT EXISTS notes (
    id              bigserial PRIMARY KEY,
    name            text NOT NULL UNIQUE,
    description     text NOT NULL DEFAULT '',
    type            text,
    priority        smallint NOT NULL DEFAULT 0,
    source_path     text,
    modified        text,           -- ISO 8601, kept verbatim (effective date, see truth)
    modified_source text,
    body            text NOT NULL DEFAULT '',
    head_tokens     text[] NOT NULL DEFAULT '{}',
    -- MAIN: compressed inline rather than TOASTed out of line (read for every candidate)
    lex_tokens      text[] STORAGE MAIN NOT NULL DEFAULT '{}',
    updated_at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS notes_lex_tokens_gin ON notes USING gin (lex_tokens);
CREATE INDEX IF NOT EXISTS notes_source_path ON notes (source_path);

-- Document frequency of each folded keyword over the notes (IDF), maintained in the same
-- transaction as notes. The empty token '' holds the number of notes.
CREATE TABLE IF NOT EXISTS lex_df (
    token   text PRIMARY KEY,
    df      integer NOT NULL
);

-- Content history of a note (contract.SeenRecord): one row per version as the indexer saw it.
-- A version is appended when the body hash OR the description changes, else the latest row
-- is refreshed. first_seen = when this BODY was first seen (carried across description-only
-- changes and renames), date_source = its provenance. Description and body are kept so the
-- "description != body" drift check can compare versions. Keyed by NAME, not notes.id: the
-- history survives a deletion, and a renamed note finds its date by content_hash.
-- Dates are ISO 8601 text, kept verbatim as the truth code wrote them.
CREATE TABLE IF NOT EXISTS note_versions (
    id            bigserial PRIMARY KEY,
    note_name     text NOT NULL,
    content_hash  text NOT NULL,
    first_seen    text NOT NULL,
    seeded        boolean NOT NULL DEFAULT false,
    date_source   text NOT NULL DEFAULT 'indexed' CHECK (date_source IN
                  ('frontmatter', 'indexed', 'migrated-mtime', 'inferred', 'transcript',
                   'inconnue')),
    description   text NOT NULL DEFAULT '',
    body          text NOT NULL DEFAULT '',
    seen_at       text NOT NULL DEFAULT '',
    last_seen     timestamptz NOT NULL DEFAULT now(),   -- last refresh of this version
    extra         jsonb NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS note_versions_name ON note_versions (note_name, id DESC);
CREATE INDEX IF NOT EXISTS note_versions_hash ON note_versions (content_hash);

-- Chunks, one partition per generation (see header).
-- tsv = the chunk's folded keywords (array_to_tsvector of search.tokens), so that the
-- lexical-only mode and the snippet choice use the same folding as the ranking.
CREATE TABLE IF NOT EXISTS chunks (
    generation_id  integer NOT NULL REFERENCES index_generations(id),
    note_id        bigint  NOT NULL REFERENCES notes(id) ON DELETE CASCADE,
    idx            integer NOT NULL,
    body           text    NOT NULL,
    -- PLAIN: never TOASTed. A 768-d halfvec is 1.5 kB; out-of-line storage would cost a
    -- TOAST lookup per row in every exact scan.
    embedding      halfvec STORAGE PLAIN NOT NULL,
    tsv            tsvector NOT NULL,
    PRIMARY KEY (generation_id, note_id, idx)
) PARTITION BY LIST (generation_id);
CREATE INDEX IF NOT EXISTS chunks_note ON chunks (note_id);
CREATE INDEX IF NOT EXISTS chunks_tsv_gin ON chunks USING gin (tsv);

-- [[wikilinks]] between notes, by name (a link may point to a note that does not exist).
CREATE TABLE IF NOT EXISTS links (
    src  text NOT NULL,
    dst  text NOT NULL,
    PRIMARY KEY (src, dst)
);
CREATE INDEX IF NOT EXISTS links_dst ON links (dst);

-- What was injected where (P0-3): which session / sub-agent received which note, in which
-- version, from which generation. Basis of the recall audit (P1-6).
CREATE TABLE IF NOT EXISTS recall_log (
    id             bigserial PRIMARY KEY,
    at             timestamptz NOT NULL DEFAULT now(),
    channel        text NOT NULL,          -- prompt | subagent | spawn | search
    session_id     text,
    agent_id       text,
    query          text,
    note_name      text NOT NULL,
    rank           integer,
    score          real,
    rerank         real,
    content_hash   text,                  -- version of the note that was injected
    generation_id  integer
);
CREATE INDEX IF NOT EXISTS recall_log_session ON recall_log (session_id, at);
CREATE INDEX IF NOT EXISTS recall_log_note ON recall_log (note_name, at);

-- ---- migrations/0006_eval.sql
-- Recall bench on the client's own corpus (P0-6) and profile switches (P0-1 b).
-- Numbered after the chantier (P0-6) so that parallel migrations do not collide.
--
-- Questions, runs and switches are HISTORY (like note_versions): they cannot be rebuilt
-- from the .md files. They reference generations by id WITHOUT a foreign key, so that the
-- results of a purged generation stay readable.

-- A question = what someone asked, and the note(s) that answer it.
--   transcript  harvested: a real prompt followed by memory_get(note) in the same session
--   client      written by a person (form, CLI)
--   generated   paraphrased from a note description by the instance's LLM: flatters the
--               model (it read the note), hence a reduced weight
CREATE TABLE IF NOT EXISTS eval_questions (
    id            bigserial PRIMARY KEY,
    question      text NOT NULL,
    expected      text[] NOT NULL CHECK (cardinality(expected) BETWEEN 1 AND 10),
    source        text NOT NULL CHECK (source IN ('transcript', 'client', 'generated')),
    weight        real NOT NULL DEFAULT 1 CHECK (weight > 0 AND weight <= 1),
    -- sha1 of source + folded question: the same prompt seen in two sessions is one question
    fingerprint   text NOT NULL UNIQUE,
    status        text NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'disabled')),
    author        text,
    session_id    text,
    seen          integer NOT NULL DEFAULT 1,      -- times harvested
    origin        jsonb NOT NULL DEFAULT '{}',
    created_at    timestamptz NOT NULL DEFAULT now(),
    updated_at    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS eval_questions_source ON eval_questions (source, status);

-- One pass of the bench over one generation with one embedder (= one profile).
CREATE TABLE IF NOT EXISTS eval_runs (
    id              bigserial PRIMARY KEY,
    generation_id   integer,
    embed_identity  text,
    profile         text,
    trigger         text NOT NULL DEFAULT 'manual',   -- manual | nightly | switch | cli
    status          text NOT NULL DEFAULT 'running'
                    CHECK (status IN ('running', 'done', 'failed')),
    started_at      timestamptz NOT NULL DEFAULT now(),
    finished_at     timestamptz,
    n_questions     integer NOT NULL DEFAULT 0,
    metrics         jsonb NOT NULL DEFAULT '{}',      -- overall + by source
    config          jsonb NOT NULL DEFAULT '{}',      -- k, rerank, weights
    regression      jsonb,                            -- set when a regression was detected
    error           text
);
CREATE INDEX IF NOT EXISTS eval_runs_generation ON eval_runs (generation_id, id DESC);

CREATE TABLE IF NOT EXISTS eval_results (
    run_id       bigint NOT NULL REFERENCES eval_runs(id) ON DELETE CASCADE,
    question_id  bigint NOT NULL,
    weight       real NOT NULL,
    rank         integer,                -- rank of the first expected note, NULL = missed
    ranked       text[] NOT NULL,        -- top 10 returned
    relevant     text[] NOT NULL,        -- expected notes that existed at run time
    ndcg10       real NOT NULL,
    PRIMARY KEY (run_id, question_id)
);

-- Small key/value state of the bench (last harvest, …).
CREATE TABLE IF NOT EXISTS eval_state (
    key         text PRIMARY KEY,
    value       jsonb NOT NULL,
    updated_at  timestamptz NOT NULL DEFAULT now()
);

-- How each generation is served: memory profile, embedding model and servers. A profile
-- change that keeps the same model only changes this row; a model change builds a new
-- generation.
CREATE TABLE IF NOT EXISTS generation_profiles (
    generation_id  integer PRIMARY KEY REFERENCES index_generations(id) ON DELETE CASCADE,
    profile        text NOT NULL,
    target         jsonb NOT NULL DEFAULT '{}',
    updated_at     timestamptz NOT NULL DEFAULT now()
);

-- A profile change (or a rollback), built in the background and gated by the bench.
CREATE TABLE IF NOT EXISTS index_switches (
    id               serial PRIMARY KEY,
    kind             text NOT NULL DEFAULT 'switch' CHECK (kind IN ('switch', 'rollback')),
    status           text NOT NULL DEFAULT 'building' CHECK (status IN
                     ('building', 'evaluating', 'switched', 'blocked', 'failed', 'cancelled',
                      'rolled_back')),
    phase            text,                        -- human-readable step
    progress         real NOT NULL DEFAULT 0,     -- 0..1
    detail           text,
    from_generation  integer,
    from_target      jsonb,
    to_generation    integer,
    to_target        jsonb NOT NULL,
    built            boolean NOT NULL DEFAULT false,  -- a new generation was built
    baseline_run     bigint,
    candidate_run    bigint,
    max_drop         real,
    comparison       jsonb,
    requested_by     text,
    decided_by       text,
    started_at       timestamptz NOT NULL DEFAULT now(),
    updated_at       timestamptz NOT NULL DEFAULT now(),
    finished_at      timestamptz
);
CREATE UNIQUE INDEX IF NOT EXISTS index_switches_one_running
    ON index_switches ((true)) WHERE status IN ('building', 'evaluating');

-- ---- migrations/0007_review.sql
-- CortHeXis review (P0-4): health history, problems tracked over time, alert state.
-- History, like note_versions: back it up with the database.

CREATE TABLE IF NOT EXISTS review_runs (
    at         timestamptz PRIMARY KEY,
    score      smallint NOT NULL CHECK (score BETWEEN 0 AND 100),
    crit       integer NOT NULL DEFAULT 0,
    warn       integer NOT NULL DEFAULT 0,
    info       integer NOT NULL DEFAULT 0,
    signature  text NOT NULL DEFAULT '',
    notes      integer NOT NULL DEFAULT 0,
    report     jsonb NOT NULL
);

-- One row per (check, note) problem: when it was first and last seen, when it went away.
-- Gives "open for more than 7 days" and the mean time to fix.
CREATE TABLE IF NOT EXISTS review_problems (
    key          text PRIMARY KEY,          -- "<check>:<note>"
    check_id     text NOT NULL,
    note         text NOT NULL,
    severity     text NOT NULL,
    first_seen   timestamptz NOT NULL,
    last_seen    timestamptz NOT NULL,
    resolved_at  timestamptz
);
CREATE INDEX IF NOT EXISTS review_problems_open ON review_problems (resolved_at)
    WHERE resolved_at IS NULL;

CREATE TABLE IF NOT EXISTS review_state (
    key         text PRIMARY KEY,
    value       text NOT NULL,
    updated_at  timestamptz NOT NULL DEFAULT now()
);

-- ---- migrations/0010_recall_turns.sql
-- Recall at every turn (P0-3): one row per recall attempt, including the ones that
-- injected nothing (below threshold, already injected, skipped prompt). recall_log only
-- holds the notes that WERE injected; this table answers "which share of the turns got a
-- recall" and "how long did the hook take", per profile.
CREATE TABLE IF NOT EXISTS recall_turns (
    id             bigserial PRIMARY KEY,
    at             timestamptz NOT NULL DEFAULT now(),
    channel        text NOT NULL,          -- prompt | subagent | spawn
    session_id     text,
    agent_id       text,
    query          text,
    candidates     integer NOT NULL DEFAULT 0,   -- hits returned by the search
    injected       integer NOT NULL DEFAULT 0,   -- notes injected in this turn
    deduplicated   integer NOT NULL DEFAULT 0,   -- relevant notes skipped: already injected
    skipped        text,                         -- why nothing was searched (short prompt…)
    degraded       boolean NOT NULL DEFAULT false,
    reranked       boolean NOT NULL DEFAULT false,
    latency_ms     integer,
    threshold      real,
    profile        text,
    generation_id  integer
);
CREATE INDEX IF NOT EXISTS recall_turns_at ON recall_turns (at);
CREATE INDEX IF NOT EXISTS recall_turns_session ON recall_turns (session_id, at);
