"""A 2.0.0 database (schema 1 + migrations 6, 7, 10, frozen in fixtures/schema_2_0_0.sql)
holding notes, links, history and a recall log is opened by 2.1: migrations 11-13 apply,
nothing is moved, every note is in the default project at the default level, and a
search / get / links without a scope gives what 2.0 gave. Skipped unless
CORTHEXIS_TEST_PG_DSN is set (see test_core_store_pg.py)."""
import os
import uuid
from pathlib import Path

import pytest

psycopg = pytest.importorskip("psycopg")
pytest.importorskip("pgvector")
pytest.importorskip("psycopg_pool")

from corthexis.store import Store  # noqa: E402

DSN = os.environ.get("CORTHEXIS_TEST_PG_DSN", "")
pytestmark = pytest.mark.skipif(not DSN, reason="CORTHEXIS_TEST_PG_DSN not set (needs Postgres)")
SCHEMA_2_0_0 = Path(__file__).parent / "fixtures" / "schema_2_0_0.sql"


@pytest.fixture()
def dsn():
    name = "cx_up20_" + uuid.uuid4().hex[:10]
    with psycopg.connect(DSN, autocommit=True) as con:
        con.execute(f"CREATE DATABASE {name}")
    d = DSN.rsplit("/", 1)[0] + "/" + name
    try:
        yield d
    finally:
        with psycopg.connect(DSN, autocommit=True) as con:
            con.execute(f"DROP DATABASE {name} WITH (FORCE)")


def _as_2_0_0(dsn):
    """The database exactly as CortHeXis 2.0.0 left it after indexing three notes."""
    with psycopg.connect(dsn, autocommit=True) as con:
        con.execute(SCHEMA_2_0_0.read_text(encoding="utf-8"))
        con.execute("INSERT INTO schema_migrations(version, name) VALUES (1, 'init'), "
                    "(6, 'eval'), (7, 'review'), (10, 'recall_turns')")
        con.execute("INSERT INTO index_generations(embed_identity, dim, status, chunk_count)"
                    " VALUES ('test:model@4', 4, 'active', 3) RETURNING id")
        con.execute("CREATE TABLE chunks_g1 PARTITION OF chunks FOR VALUES IN (1)")
        for name, desc, body in (("deploy-runbook", "How we deploy", "ssh then restart"),
                                 ("http-202-is-not-success", "202 means accepted",
                                  "verify against a witness"),
                                 ("team-calendar", "Who is where", "see [[deploy-runbook]]")):
            r = con.execute(
                "INSERT INTO notes(name, description, body, source_path, modified,"
                " modified_source, lex_tokens, head_tokens) VALUES (%s, %s, %s, %s,"
                " '2026-09-01', 'frontmatter', %s, %s) RETURNING id",
                (name, desc, body, f"/m/{name.replace('-', '_')}.md",
                 (desc + " " + body).lower().split(), desc.lower().split())).fetchone()[0]
            con.execute("INSERT INTO chunks(generation_id, note_id, idx, body, embedding, tsv)"
                        " VALUES (1, %s, 0, %s, '[1,0,0,0]', to_tsvector('simple', %s))",
                        (r, body, body))
            con.execute("INSERT INTO note_versions(note_name, content_hash, first_seen, body)"
                        " VALUES (%s, %s, '2026-09-01', %s)", (name, "h-" + name, body))
        con.execute("INSERT INTO lex_df(token, df) VALUES ('', 3)")
        con.execute("INSERT INTO links(src, dst) VALUES ('team-calendar', 'deploy-runbook')")
        con.execute("INSERT INTO recall_log(channel, session_id, note_name, rank, score)"
                    " VALUES ('prompt', 's-old', 'deploy-runbook', 1, 0.9)")


def test_a_2_0_0_database_is_upgraded_in_place(dsn):
    _as_2_0_0(dsn)
    st = Store(dsn)
    try:
        assert st.schema_version() == 13
        with st.pool.connection() as con:
            versions = [r["version"] for r in con.execute(
                "SELECT version FROM schema_migrations ORDER BY version")]
            assert versions == [1, 6, 7, 10, 11, 12, 13]
            rows = con.execute("SELECT name, project, level FROM notes ORDER BY name").fetchall()
            assert [(r["project"], r["level"]) for r in rows] == [("default", 2)] * 3
            assert con.execute("SELECT project FROM links").fetchone()["project"] == "default"
            assert con.execute("SELECT project, level FROM recall_log").fetchone() == {
                "project": None, "level": None}            # rows before 2.1: unknown = default
            assert con.execute("SELECT count(*) AS n FROM note_access").fetchone()["n"] == 0
        # the reads of 2.0, unscoped: unchanged
        n = st.get_note("deploy-runbook")
        assert n is not None and n.project == "default" and n.level == 2
        assert st.resolve_note("deploy-runbook").body == "ssh then restart"
        assert st.find_note_by_path("deploy_runbook") == "deploy-runbook"
        hits = st.search([1.0, 0, 0, 0], "deploy restart", 5)
        assert hits and hits[0].note_name == "deploy-runbook"
        assert all(h.project == "default" and h.level == 2 for h in hits)
        assert [h.note_name for h in st.search([1.0, 0, 0, 0], "deploy restart", 5,
                                               projects=["default"])] == \
            [h.note_name for h in hits]
        assert st.search([1.0, 0, 0, 0], "deploy restart", 5, projects=["radio"]) == []
        assert [r["name"] for r in st.links("team-calendar")["links"]] == ["deploy-runbook"]
        assert st.note_versions("deploy-runbook")[0].content_hash == "h-deploy-runbook"
        listed = {r["name"]: r for r in st.list_notes()}
        assert listed["team-calendar"]["links"] == ["deploy-runbook"]
        assert listed["deploy-runbook"]["backlinks"] == ["team-calendar"]
        assert all(r["project"] == "default" and r["level"] == "project" for r in listed.values())
        # the recall log of 2.0 is still read (NULL project = default)
        assert {r["note_name"] for r in st.recall_log(projects=["default"])} == {"deploy-runbook"}
    finally:
        st.close()


def test_migrate_is_idempotent_and_runs_once(dsn):
    _as_2_0_0(dsn)
    assert Store(dsn, migrate=False).migrate() == [11, 12, 13]
    st = Store(dsn)
    try:
        assert st.migrate() == []
    finally:
        st.close()
