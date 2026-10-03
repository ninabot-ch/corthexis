"""The standalone service: search/get API, MCP tools, recall hook client, notifications,
dashboard endpoints (auth, graph, repairs with approval, recall hook endpoint)."""
import http.server
import importlib
import json
import os
import sys
import threading
import uuid

import pytest

from corthexis import hook, notify

DSN = os.environ.get("CORTHEXIS_TEST_PG_DSN", "")
TOKEN = "t" * 40


# --------------------------------------------------------------------------- hook client
def test_hook_install_merges_and_uninstall_keeps_other_hooks(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    settings = tmp_path / "claude" / "settings.json"
    settings.parent.mkdir()
    other = {"type": "command", "command": "echo mine"}
    settings.write_text(json.dumps({"model": "x", "hooks": {"UserPromptSubmit": [
        {"hooks": [other]}]}}))
    for _ in range(2):                                    # idempotent
        hook.install("user", "http://svc:8420/", TOKEN, None)
    doc = json.loads(settings.read_text())
    assert doc["model"] == "x"
    ups = doc["hooks"]["UserPromptSubmit"]
    assert ups[0]["hooks"] == [other] and len(ups) == 2
    assert "corthexis" in ups[1]["hooks"][0]["command"]
    assert doc["hooks"]["PreToolUse"][0]["matcher"] == "Task|Agent"
    cfg = json.loads((tmp_path / "xdg" / "corthexis" / "hook.json").read_text())
    assert cfg["url"] == "http://svc:8420"
    tok = tmp_path / "xdg" / "corthexis" / "token"
    assert tok.read_text().strip() == TOKEN and (tok.stat().st_mode & 0o077) == 0
    assert hook.load_config()["token"] == TOKEN
    hook.uninstall("user")
    doc = json.loads(settings.read_text())
    assert doc["hooks"] == {"UserPromptSubmit": [{"hooks": [other]}]}


def test_hook_scopes(tmp_path):
    assert hook.settings_path("project", str(tmp_path)) == tmp_path / ".claude" / "settings.json"
    assert hook.settings_path("local", str(tmp_path)).name == "settings.local.json"


class _Recorder(http.server.BaseHTTPRequestHandler):
    seen: list = []
    answer = b"{}"

    def do_POST(self):  # noqa: N802
        n = int(self.headers.get("content-length") or 0)
        _Recorder.seen.append((self.path, self.headers.get("authorization"),
                               self.headers.get("content-type"), self.rfile.read(n)))
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.end_headers()
        self.wfile.write(_Recorder.answer)

    def log_message(self, *a):
        pass


@pytest.fixture()
def http_sink():
    _Recorder.seen = []
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Recorder)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def test_hook_run_posts_to_the_service(http_sink, monkeypatch, capsys):
    _Recorder.answer = b'{"hookSpecificOutput": {"additionalContext": "x"}}'
    monkeypatch.setenv("CORTHEXIS_URL", http_sink)
    monkeypatch.setenv("CORTHEXIS_TOKEN", TOKEN)
    monkeypatch.setenv("XDG_CONFIG_HOME", "/nonexistent")
    raw = json.dumps({"hook_event_name": "UserPromptSubmit", "prompt": "hello there"}).encode()
    assert hook.via_api(raw, hook.load_config())
    path, auth, _ct, body = _Recorder.seen[-1]
    assert path == "/api/recall/hook" and auth == f"Bearer {TOKEN}" and body == raw
    assert "additionalContext" in capsys.readouterr().out


def test_hook_without_service_is_silent(monkeypatch, capsys):
    monkeypatch.setenv("CORTHEXIS_URL", "http://127.0.0.1:9")
    monkeypatch.setenv("CORTHEXIS_TOKEN", TOKEN)
    monkeypatch.delenv("CORTHEXIS_DATABASE_URL", raising=False)
    monkeypatch.setattr(sys, "stdin", type("S", (), {"buffer": __import__("io").BytesIO(
        b'{"hook_event_name":"UserPromptSubmit","prompt":"anything at all"}')})())
    assert hook.run() == 0
    assert capsys.readouterr().out == ""


# --------------------------------------------------------------------------- notify
def test_notify_formats_and_secret_free_labels(http_sink, monkeypatch):
    monkeypatch.setenv("CORTHEXIS_NOTIFY_WEBHOOKS",
                       f"{http_sink}/hook?key=SECRET,slack+{http_sink}/s,ntfy+{http_sink}/t")
    monkeypatch.delenv("CORTHEXIS_NOTIFY_CMD", raising=False)
    out = notify.send("Title", "Body", "http://x/", "memory")
    assert list(out.values()) == ["ok", "ok", "ok"]
    assert all("SECRET" not in k for k in out)
    j, s, n = _Recorder.seen[-3:]
    assert json.loads(j[3]) == {"title": "Title", "text": "Body", "link": "http://x/",
                                "kind": "memory", "source": "corthexis"}
    assert json.loads(s[3]) == {"text": "Title\nBody\nhttp://x/"}
    assert n[3] == b"Body\nhttp://x/"


def test_notify_command(monkeypatch, tmp_path):
    out_file = tmp_path / "msg"
    monkeypatch.delenv("CORTHEXIS_NOTIFY_WEBHOOKS", raising=False)
    monkeypatch.setenv("CORTHEXIS_NOTIFY_CMD", f"cat > {out_file}")
    assert notify.enabled()
    assert notify.send("T", "B")["cmd"] == "ok"
    assert out_file.read_text() == "T\nB"


# --------------------------------------------------------------------------- MCP
def test_bearer_check(monkeypatch):
    from corthexis import server
    monkeypatch.setenv("CORTHEXIS_TOKEN", TOKEN)
    assert server.check_bearer(f"Bearer {TOKEN}")
    assert not server.check_bearer("Bearer nope")
    assert not server.check_bearer(None)
    monkeypatch.setenv("CORTHEXIS_TOKEN", "short")
    assert not server.check_bearer("Bearer short")      # too short: never accepted


def test_mcp_tools_are_declared():
    import asyncio

    from corthexis import server
    tools = asyncio.run(server.mcp.list_tools())
    assert {t.name for t in tools} == {"memory_search", "memory_get", "memory_links"}


# --------------------------------------------------------------------------- with Postgres
needs_pg = pytest.mark.skipif(not DSN, reason="CORTHEXIS_TEST_PG_DSN not set (needs Postgres)")


@pytest.fixture()
def live(tmp_path, monkeypatch):
    """A real store indexed with the fictional corpus, the bag-of-words embedder."""
    psycopg = pytest.importorskip("psycopg")
    from test_recall_pg import BowEmbedder

    from corthexis import bench_recall as B
    from corthexis import service, switch
    from corthexis.indexer import IndexConfig, IndexRunner
    from corthexis.store import Store

    name = "cx_svc_" + uuid.uuid4().hex[:10]
    with psycopg.connect(DSN, autocommit=True) as con:
        con.execute(f"CREATE DATABASE {name}")
    dsn = DSN.rsplit("/", 1)[0] + "/" + name
    mem = B.write_corpus(tmp_path / "notes")
    (mem / "dangling.md").write_text(
        "---\nname: dangling\ndescription: points nowhere\nmetadata:\n  type: reference\n"
        "  modified: 2026-09-01\n---\nSee [[packaging-supplier-old]].\n", encoding="utf-8")
    store = Store(dsn)
    IndexRunner(lambda: store, BowEmbedder, IndexConfig(memory_dir=mem), log=lambda m: None
                ).run_once()
    monkeypatch.setenv("CORTHEXIS_DATABASE_URL", dsn)
    monkeypatch.setenv("CORTHEXIS_MEMORY_DIR", str(mem))
    monkeypatch.setenv("CORTHEXIS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(service, "_store", store)
    monkeypatch.setattr(service, "_qemb", None)
    monkeypatch.setattr(switch, "serving_embedder", lambda st, *a, **k: BowEmbedder())
    try:
        yield store, mem
    finally:
        service._store = None
        store.close()
        with psycopg.connect(DSN, autocommit=True) as con:
            con.execute(f"DROP DATABASE {name} WITH (FORCE)")


@needs_pg
def test_service_search_get_links(live):
    from corthexis import service
    rows = service.search("Kartonage Weiss cardboard boxes", 3)
    assert rows[0]["note_name"] == "packaging-supplier" and "degraded" not in rows[0]
    assert {"age_days", "date_source", "generation", "snippet"} <= set(rows[0])
    body = service.get("packaging-supplier")
    assert body.startswith("[note packaging-supplier — ")
    assert service.get("packaging_supplier.md") == body          # by file name too
    assert service.get("no-such-note") is None
    assert service.search("")[0]["error"]
    assert service.status()["generation"]["embed_identity"] == "test:bow@64"


@needs_pg
def test_service_degrades_to_keywords(live, monkeypatch):
    from corthexis import service

    class Down:
        rerank_policy = "off"

        def identity(self):
            return "test:bow@64"

        def embed_query(self, *_a, **_k):
            raise RuntimeError("server down")
    monkeypatch.setattr(service, "embedder", lambda: Down())
    rows = service.search("Kartonage Weiss cardboard", 3)
    assert rows[0]["note_name"] == "packaging-supplier"
    assert "keyword-only" in rows[0]["degraded"]


@pytest.fixture()
def client(live, monkeypatch):
    from fastapi.testclient import TestClient
    monkeypatch.setenv("CORTHEXIS_TOKEN", TOKEN)
    monkeypatch.setenv("CORTHEXIS_WORKERS", "0")
    monkeypatch.setenv("CORTHEXIS_REVIEW_MCP_HANDSHAKE", "0")
    monkeypatch.setenv("CORTHEXIS_EMBED_URLS", "http://127.0.0.1:9")
    import corthexis.dashboard.app as appmod
    appmod = importlib.reload(appmod)
    with TestClient(appmod.app) as c:
        yield c, appmod, live[1]


AUTH = {"authorization": f"Bearer {TOKEN}"}


@needs_pg
def test_dashboard_auth_and_reads(client):
    c, _m, _mem = client
    assert c.get("/api/info").json()["writable"] is True
    assert c.get("/api/graph").status_code == 401
    assert c.post("/mcp", json={}).status_code == 401
    g = c.get("/api/graph", headers=AUTH).json()
    assert g["stats"]["notes"] >= 10 and any(gh["label"] == "packaging-supplier-old"
                                             for gh in g["ghosts"])
    assert c.get(f"/api/graph?since={g['version']}", headers=AUTH).json()["unchanged"]
    s = c.get("/api/search?q=Kartonage Weiss cardboard", headers=AUTH).json()
    assert s["results"][0]["note"] == "packaging-supplier"
    n = c.get("/api/note/dangling", headers=AUTH).json()
    assert n["out"] == [{"target": "packaging-supplier-old", "resolved": None}]
    assert c.get("/api/note/..%2Fetc", headers=AUTH).status_code in (400, 404)
    assert c.get("/healthz").text in ("ok",) or c.get("/healthz").status_code == 503


@needs_pg
def test_dashboard_repair_needs_approval(client):
    c, _m, mem = client
    before = (mem / "dangling.md").read_text()
    body = {"kind": "relink", "target": "packaging-supplier-old",
            "new_target": "packaging-supplier", "note": "dangling"}
    assert c.post("/api/proposals", json=body).status_code == 401
    p = c.post("/api/proposals", json=body, headers=AUTH).json()
    assert p["status"] == "pending" and "+See [[packaging-supplier]]." in p["changes"][0]["diff"]
    assert (mem / "dangling.md").read_text() == before          # nothing written yet
    r = c.post(f"/api/proposals/{p['id']}/approve", headers=AUTH).json()
    assert r["status"] == "applied"
    assert "[[packaging-supplier]]" in (mem / "dangling.md").read_text()
    assert c.post(f"/api/proposals/{p['id']}/approve", headers=AUTH).status_code == 409


@needs_pg
def test_dashboard_review_and_recall_hook(client):
    c, _m, _mem = client
    rep = c.post("/api/review/run", headers=AUTH).json()["report"]
    assert "broken_links" in {f["id"] for f in rep["findings"]}
    payload = {"hook_event_name": "UserPromptSubmit", "session_id": "s1",
               "prompt": "Kartonage Weiss cardboard packaging boxes: who do we order from?"}
    assert c.post("/api/recall/hook", json=payload).status_code == 401
    out = c.post("/api/recall/hook", json=payload, headers=AUTH).json()
    assert "packaging-supplier" in out["hookSpecificOutput"]["additionalContext"]
    again = c.post("/api/recall/hook", json=payload, headers=AUTH).json()
    assert "packaging-supplier`" not in json.dumps(again)        # not twice in a session


@needs_pg
def test_demo_mode_is_read_only(live, monkeypatch):
    from fastapi.testclient import TestClient
    monkeypatch.setenv("CORTHEXIS_TOKEN", TOKEN)
    monkeypatch.setenv("CORTHEXIS_DEMO", "1")
    monkeypatch.setenv("CORTHEXIS_WORKERS", "0")
    import corthexis.dashboard.app as appmod
    appmod = importlib.reload(appmod)
    with TestClient(appmod.app) as c:
        assert c.get("/api/graph").status_code == 200              # public read
        assert c.post("/api/review/run", headers=AUTH).status_code == 403
        assert c.post("/api/proposals", json={"kind": "close", "note": "x"},
                      headers=AUTH).status_code == 403
        assert 'data-demo="1"' in c.get("/").text
        assert c.post("/mcp", json={}, headers=AUTH).status_code == 404
    monkeypatch.delenv("CORTHEXIS_DEMO")
    importlib.reload(appmod)
