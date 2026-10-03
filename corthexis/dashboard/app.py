"""CortHeXis service: dashboard, MCP over HTTP, recall hook endpoint, indexer and reviewer.

One process (``corthexis serve``) runs everything a standalone install needs:

* the **indexer** (``indexer.IndexRunner``): watches the notes folder, re-embeds what
  changed, builds a new index generation when the embedding model changes;
* the **reviewer**: reads the memory back every hour (``review.run_review``), keeps the
  score history, sends a digest a day when the findings changed and right away on a new
  critical problem, through ``notify`` (webhooks or a command — nothing hard-wired);
* the **dashboard** (``/``): the graph of the notes, recall, health, repairs with
  approval, bench;
* **MCP** at ``/mcp`` (streamable HTTP, Bearer ``CORTHEXIS_TOKEN``) and the **recall hook**
  endpoint ``POST /api/recall/hook``.

Access. ``CORTHEXIS_TOKEN`` (≥ 32 characters) guards MCP, the hook endpoint and every
write (review run, repair proposals and approvals, bench run). Reading the dashboard needs
it too unless ``CORTHEXIS_DASHBOARD_PUBLIC=1``. ``CORTHEXIS_DEMO=1`` = a public read-only
showcase: no write at all, a banner. Bind the port to loopback (the compose default) or put
the service behind your own access proxy.

Repairs are proposals: a click computes the complete change (unified diff of every file it
touches) and nothing is written until someone approves it; ``apply`` refuses when a file
changed in between and keeps a copy of what it overwrites.
"""
from __future__ import annotations

import asyncio
import contextlib
import datetime as dt
import hashlib
import json
import logging
import os
import sys
import threading
import time
from pathlib import Path

from fastapi import Body, FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .. import __version__, notify, service
from .. import repair as rp
from .. import review as rv
from ..config import env, env_bool, env_int, env_list
from ..dates import age_days

log = logging.getLogger("corthexis")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
# httpx logs full URLs; a webhook URL may carry a secret
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

STATIC = Path(__file__).resolve().parent / "static"
DEMO = env_bool("DEMO", False)
PUBLIC_READ = DEMO or env_bool("DASHBOARD_PUBLIC", False)
STARTED_AT = time.time()

_lock = threading.RLock()
_state: dict = {"report": None, "graph": None, "graph_key": None, "running": False,
                "runner": None}


# ----------------------------------------------------------------------------- access

def _bearer(request: Request) -> str | None:
    h = request.headers.get("authorization")
    if h:
        return h
    c = request.cookies.get("corthexis_token")
    return f"Bearer {c}" if c else None


def require_read(request: Request) -> None:
    if PUBLIC_READ:
        return
    from ..server import check_bearer
    if not check_bearer(_bearer(request)):
        raise HTTPException(401, "token required", headers={"WWW-Authenticate": "Bearer"})


def require_write(request: Request) -> str:
    if DEMO:
        raise HTTPException(403, "read-only demonstration")
    from ..server import check_bearer, token
    if len(token()) < 32:
        raise HTTPException(503, "writes are disabled: set CORTHEXIS_TOKEN (32+ characters)")
    if not check_bearer(_bearer(request)):
        raise HTTPException(401, "token required", headers={"WWW-Authenticate": "Bearer"})
    return request.headers.get("x-corthexis-user") or "dashboard"


# ----------------------------------------------------------------------------- review

def source():
    return rv.PgSource(service.get_store())


_history: dict = {"obj": None}


def history():
    if _history["obj"] is None:
        _history["obj"] = rv.PgHistory(service.get_store())
    return _history["obj"]


def review_config() -> rv.ReviewConfig:
    return rv.ReviewConfig.from_env(memory_dir=service.memory_dir())


def chain_config() -> rv.ChainConfig:
    """The chain as a session sees it, from inside the service: the MCP server answers
    when started the way an agent starts it, the model servers are up."""
    launch = None
    if env_bool("REVIEW_MCP_HANDSHAKE", True):
        launch = {"command": sys.executable, "args": ["-m", "corthexis.server"]}
    cfg = rv.ChainConfig.from_env(launch=launch,
                                  handshake_timeout=float(env("REVIEW_HANDSHAKE_TIMEOUT", "60")))
    if not cfg.embed_urls:
        try:
            from .. import embed
            d = embed.describe()
            if d.get("backend") == "llamacpp":
                cfg.embed_urls = list(d.get("urls") or [])
                cfg.rerank_url = d.get("rerank_url")
        except Exception:  # noqa: BLE001 — nothing configured: nothing to probe
            pass
    return cfg


def bench_findings() -> list:
    try:
        from .. import eval as ev
        return rv.external_findings(ev.findings(service.get_store()))
    except Exception as e:  # noqa: BLE001 — no bench yet
        log.debug("bench findings unavailable: %s", e)
        return []


def index_findings() -> list:
    """The indexer is part of the chain: a pass that keeps failing means new notes are
    not searchable."""
    runner = _state.get("runner")
    if runner is None or not runner.last_error:
        return []
    return [rv.Finding(
        "indexer_failing", "crit", "chain", "New notes are not indexed",
        f"The last indexing pass failed: {runner.last_error[:200]}",
        remedy="Check the embedding service and the database (corthexis status).", count=1)]


def run_review(*, chain: bool | None = None, alert: bool = True) -> dict:
    chain = env_bool("REVIEW_CHAIN", True) if chain is None else chain
    with _lock:
        if _state["running"]:
            return _state["report"] or {}
        _state["running"] = True
    try:
        findings = rv.chain_checks(chain_config()) if chain else []
        findings += index_findings() + bench_findings()
        try:
            src = source()
        except Exception as e:  # noqa: BLE001 — database down: review the files alone
            log.warning("review without the index: %s", e)
            src = None
        report = rv.run_review(review_config(), src, chain=findings)
        try:
            history().record(report)
        except Exception as e:  # noqa: BLE001
            log.warning("review history not recorded: %s", e)
        with _lock:
            _state["report"] = report
            _state["graph"] = None
        if alert:
            maybe_alert(report)
        return report
    finally:
        with _lock:
            _state["running"] = False


def current() -> dict:
    rep = _state["report"]
    if rep is None:
        try:
            rep = history().last_report()
        except Exception:  # noqa: BLE001
            rep = None
        if rep is None:
            rep = run_review(chain=False, alert=False)
        _state["report"] = rep
    return rep


def maybe_alert(report: dict, *, force: bool = False) -> str | None:
    if DEMO or not notify.enabled():
        return None
    h = history()
    kind = "digest" if force else rv.alert_decision(report, h, rv.AlertPolicy.from_env())
    if not kind:
        return None
    title, body = rv.format_digest(report, kind=kind)
    sent = notify.send(title, body, notify.public_url("/?tab=review"), "memory")
    if any(v == "ok" for v in sent.values()):
        rv.mark_alerted(report, h, kind)
        return kind
    return None


def review_loop() -> None:
    every = env_int("REVIEW_EVERY_S", 3600)
    if every <= 0:
        return
    time.sleep(float(env("REVIEW_FIRST_DELAY_S", "20") or 20))
    while True:
        try:
            rep = run_review()
            log.info("review: score %s, %s", rep.get("score"), rep.get("counts"))
        except Exception as e:  # noqa: BLE001
            log.warning("review failed: %s", e)
        pol = rv.AlertPolicy.from_env()
        now = dt.datetime.now(dt.timezone.utc)
        try:
            from zoneinfo import ZoneInfo
            now = now.astimezone(ZoneInfo(pol.tz))
        except Exception:  # noqa: BLE001
            pass
        slot = now.replace(hour=pol.digest_hour, minute=pol.digest_minute, second=5,
                           microsecond=0)
        if slot <= now:
            slot += dt.timedelta(days=1)
        time.sleep(max(30.0, min(every, (slot - now).total_seconds())))


# ----------------------------------------------------------------------------- indexer

def start_indexer():
    if not env_bool("INDEXER", True) or DEMO and not env_bool("DEMO_INDEXER", True):
        return None
    from .. import embed
    from ..indexer import IndexConfig, IndexRunner

    def on_report(rep) -> None:
        with _lock:
            _state["graph"] = None

    runner = IndexRunner(service.get_store, embed.get,
                         IndexConfig.from_env(memory_dir=service.memory_dir()),
                         log=lambda m: log.info(m), on_report=on_report)
    _state["runner"] = runner
    runner.start()
    return runner


# ----------------------------------------------------------------------------- graph

def _pca2d(vecs: dict[str, list[float]]) -> dict[str, tuple[float, float]]:
    import numpy as np
    if len(vecs) < 3:
        return {k: (0.0, 0.0) for k in vecs}
    names = list(vecs)
    M = np.asarray([vecs[k] for k in names], dtype=np.float32)
    M = M - M.mean(axis=0)
    _u, _s, vt = np.linalg.svd(M, full_matrices=False)
    P = M @ vt[:2].T
    for c in range(2):
        col = P[:, c]
        rng = float(col.max() - col.min()) or 1.0
        P[:, c] = (col - col.min()) / rng * 2 - 1
    return {k: (round(float(P[i, 0]), 4), round(float(P[i, 1]), 4)) for i, k in enumerate(names)}


def _family(name: str) -> str:
    for sep in "-_ ":
        if sep in name:
            return name.split(sep, 1)[0].lower()
    return name.lower()


def _corpus_key(notes, report: dict, gen) -> str:
    h = hashlib.sha1()
    for n in notes:
        h.update(f"{n.path.name}:{n.mtime}:{n.words}\n".encode())
    h.update((report or {}).get("signature", "").encode())
    h.update((report or {}).get("at", "").encode())
    h.update(str(gen).encode())
    return h.hexdigest()[:12]


def _active_gen():
    try:
        g = service.get_store().active_generation()
        return (g.id, g.chunk_count) if g else None
    except Exception:  # noqa: BLE001
        return None


def graph() -> dict:
    notes = rv.load_corpus(service.memory_dir())
    report = current()
    key = _corpus_key(notes, report, _active_gen())
    with _lock:
        if _state["graph"] and _state["graph_key"] == key:
            return _state["graph"]
    resolve = rv.Resolver(notes)
    indexed, cents, err, label = {}, {}, None, None
    try:
        src = source()
        label = src.label
        indexed = src.indexed()
        cents = src.centroids()
    except Exception as e:  # noqa: BLE001
        err = str(e)[:200]
    names = {n.name for n in notes}
    proj = _pca2d({k: v for k, v in cents.items() if k in names})
    flags = report.get("flags") or {}
    sev = {f["id"]: f["severity"] for f in report.get("findings", [])}
    edges, ghosts, inbound = [], {}, {}
    for n in notes:
        seen = set()
        for t in n.links:
            r = resolve(t)
            if r and r.name != n.name and r.name not in seen:
                seen.add(r.name)
                edges.append({"s": n.name, "t": r.name})
                inbound[r.name] = inbound.get(r.name, 0) + 1
            elif not r:
                gid = f"ghost:{t}"
                ghosts.setdefault(gid, {"id": gid, "label": t, "ghost": True, "refs": 0})
                ghosts[gid]["refs"] += 1
                edges.append({"s": n.name, "t": gid, "broken": True})
    nodes = []
    for n in notes:
        ix = indexed.get(n.name)
        modified = (ix.modified if ix else None) or n.parsed.modified
        f_ids = flags.get(n.name, [])
        level = ("crit" if any(sev.get(i) == "crit" for i in f_ids) else
                 "warn" if any(sev.get(i) == "warn" for i in f_ids) else
                 "info" if f_ids else None)
        sx, sy = proj.get(n.name, (None, None))
        nodes.append({
            "id": n.name, "file": n.path.name, "type": n.type or "unknown",
            "family": _family(n.name), "desc": n.description, "words": n.words,
            "priority": bool(n.parsed.priority),
            "modified": (str(modified) if modified else "")[:10] or None,
            "age": age_days(str(modified) if modified else None),
            "date_source": (ix.modified_source if ix else None)
            or ("frontmatter" if n.parsed.modified else "unknown"),
            "in": inbound.get(n.name, 0), "out": len([t for t in n.links if resolve(t)]),
            "flags": f_ids, "level": level, "sx": sx, "sy": sy, "indexed": ix is not None,
            "chunks": ix.chunks if ix else 0,
        })
    g = {
        "version": key, "at": report.get("at"), "source": label, "error": err,
        "stats": {"notes": len(notes), "words": sum(n.words for n in notes),
                  "links": len([e for e in edges if not e.get("broken")]),
                  "broken": len([e for e in edges if e.get("broken")]),
                  "chunks": sum(v.chunks for v in indexed.values()) if indexed else None,
                  "families": len({_family(n.name) for n in notes}),
                  "types": {t: sum(1 for n in notes if (n.type or "unknown") == t)
                            for t in sorted({n.type or "unknown" for n in notes})}},
        "nodes": nodes, "ghosts": list(ghosts.values()), "edges": edges,
    }
    with _lock:
        _state["graph"], _state["graph_key"] = g, key
    return g


def note(name: str) -> dict:
    notes = rv.load_corpus(service.memory_dir())
    resolve = rv.Resolver(notes)
    n = resolve(name)
    if not n:
        raise HTTPException(404, "unknown note")
    inbound = sorted({m.name for m in notes if m.name != n.name
                      and any((r := resolve(t)) is not None and r.name == n.name
                              for t in m.links)})
    outbound = [{"target": t, "resolved": (r.name if (r := resolve(t)) else None)}
                for t in n.links]
    ix = None
    with contextlib.suppress(Exception):
        ix = source().indexed().get(n.name)
    modified = (ix.modified if ix else None) or n.parsed.modified
    report = current()
    flags = []
    for f in report.get("findings", []):
        if n.name not in (f.get("notes") or []):
            continue
        items = [i for i in f.get("items") or [] if i.get("note") == n.name
                 or i.get("other") == n.name]
        flags.append({"id": f["id"], "severity": f["severity"], "category": f["category"],
                      "title": f["title"], "remedy": f["remedy"], "action": f.get("action"),
                      "judgement": f.get("judgement"), "items": items})
    return {
        "id": n.name, "file": n.path.name, "type": n.type or "unknown", "desc": n.description,
        "words": n.words, "priority": bool(n.parsed.priority),
        "modified": (str(modified) if modified else "")[:10] or None,
        "age": age_days(str(modified) if modified else None),
        "date_source": (ix.modified_source if ix else None)
        or ("frontmatter" if n.parsed.modified else "unknown"),
        "chunks": ix.chunks if ix else None, "indexed": ix is not None,
        "warnings": [w for w in n.parsed.warnings if w != "empty description"],
        "body": n.body, "in": inbound, "out": outbound, "flags": flags,
    }


def stats() -> dict:
    g = graph()
    buckets: dict[str, int] = {}
    for n in g["nodes"]:
        if n["modified"]:
            with contextlib.suppress(ValueError):
                wk = dt.date.fromisoformat(n["modified"]).isocalendar()
                key = f"{wk[0]}-W{wk[1]:02d}"
                buckets[key] = buckets.get(key, 0) + 1
    ages = sorted(n["age"] for n in g["nodes"] if n["age"] is not None)

    def pct(p):
        return float(ages[min(len(ages) - 1, int(p * len(ages)))]) if ages else None
    rep = _state["report"] or {}
    return {"stats": g["stats"], "weekly": dict(sorted(buckets.items())),
            "age": {"median": pct(.5), "p90": pct(.9),
                    "fresh_7d": sum(1 for a in ages if a <= 7),
                    "fresh_30d": sum(1 for a in ages if a <= 30)},
            "review": {"score": rep.get("score"), "at": rep.get("at")}}


# ----------------------------------------------------------------------------- proposals

def _proposals_file() -> Path:
    return service.data_dir() / "proposals.json"


_plock = threading.Lock()


def _load() -> dict:
    try:
        return json.loads(_proposals_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save(d: dict) -> None:
    p = _proposals_file()
    p.parent.mkdir(parents=True, exist_ok=True)
    keep = dict(sorted(d.items(), key=lambda kv: kv[1].get("created_at", 0))[-200:])
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(keep, ensure_ascii=False), encoding="utf-8")
    os.chmod(tmp, 0o600)
    tmp.replace(p)


def proposals() -> list[dict]:
    with _plock:
        d = _load()
    return sorted(d.values(), key=lambda p: -p.get("created_at", 0))


class ProposalIn(BaseModel):
    kind: str                 # relink | merge | rename | close
    note: str = ""
    target: str = ""
    new_target: str = ""
    keep: str = ""
    drop: str = ""
    new_name: str = ""
    reason: str = ""


def propose(body: ProposalIn, user: str) -> dict:
    mem = service.memory_dir()
    try:
        if body.kind == "relink":
            if not (body.target and body.new_target):
                raise rp.RepairError("relink needs the broken target and the note to point to")
            p = rp.propose_relink(mem, body.target, body.new_target,
                                  [body.note] if body.note else None)
        elif body.kind == "merge":
            p = rp.propose_merge(mem, body.keep, body.drop)
        elif body.kind == "rename":
            p = rp.propose_rename(mem, body.note, body.new_name or None)
        elif body.kind == "close":
            p = rp.propose_close(mem, body.note, body.reason)
        else:
            raise rp.RepairError(f"unknown repair {body.kind!r}")
    except rp.RepairError as e:
        raise HTTPException(400, str(e)) from e
    rec = {**p.to_dict(), "status": "pending", "created_by": user, "created_at": time.time()}
    with _plock:
        d = _load()
        d[p.id] = rec
        _save(d)
    log.info("repair proposed by %s: %s", user, p.title)
    _arm_ping(p.id, p.title)
    return rec


def _arm_ping(pid: str, title: str) -> None:
    """A proposal nobody answers pings the channels once."""
    delay = float(env("REVIEW_PENDING_PING_S", "600") or 600)
    if delay <= 0 or not notify.enabled():
        return

    def ping() -> None:
        with contextlib.suppress(Exception):
            if _load().get(pid, {}).get("status") == "pending":
                notify.send("CortHeXis — a memory repair waits for approval", title,
                            notify.public_url(f"/?tab=repairs&proposal={pid}"), "repair")
    t = threading.Timer(delay, ping)
    t.daemon = True
    t.start()


def decide(pid: str, approve: bool, user: str) -> dict:
    with _plock:
        d = _load()
        rec = d.get(pid)
        if not rec:
            raise HTTPException(404, "unknown proposal")
        if rec["status"] != "pending":
            raise HTTPException(409, f"proposal already {rec['status']}")
        rec.update(decided_by=user, decided_at=time.time())
        if not approve:
            rec["status"] = "refused"
            _save(d)
            return rec
        try:
            written = rp.apply(service.memory_dir(), rp.Proposal.from_dict(rec),
                               backup_dir=service.data_dir() / "backups")
        except rp.Conflict as e:
            rec.update(status="conflict", error=str(e))
            _save(d)
            raise HTTPException(409, f"{e} — the note was edited meanwhile; propose again") from e
        except (OSError, rp.RepairError) as e:
            rec.update(status="failed", error=str(e))
            _save(d)
            raise HTTPException(500, f"repair failed: {e}") from e
        rec.update(status="applied", written=written)
        _save(d)
    log.info("repair applied by %s: %s (%d file(s))", user, rec["title"], len(written))
    threading.Thread(target=_after_repair, daemon=True, name="corthexis-after").start()
    return rec


def _after_repair() -> None:
    runner = _state.get("runner")
    if runner is not None:
        runner.run_once()
    run_review(chain=False, alert=False)


# ----------------------------------------------------------------------------- app

def _mcp_app():
    from ..server import http_app, mcp
    return http_app(env_list("ALLOWED_HOSTS")), mcp


_mcp_sub, _mcp = _mcp_app()


@contextlib.asynccontextmanager
async def lifespan(_app: FastAPI):
    if env_bool("WORKERS", True):
        start_indexer()
        threading.Thread(target=review_loop, daemon=True, name="corthexis-review").start()
    async with _mcp.session_manager.run():
        yield


app = FastAPI(title="CortHeXis", docs_url=None, redoc_url=None, openapi_url=None,
              lifespan=lifespan)
REDIRECT_HOSTS = {h.lower() for h in env_list("REDIRECT_HOSTS")}


@app.middleware("http")
async def _gate(request: Request, call_next):
    path = request.url.path
    host = request.headers.get("host", "").split(":")[0].lower()
    if REDIRECT_HOSTS and host in REDIRECT_HOSTS and path != "/healthz" and notify.public_url():
        q = f"?{request.url.query}" if request.url.query else ""
        return RedirectResponse(notify.public_url(path) + q, status_code=301)
    if path == "/mcp" or path.startswith("/mcp/"):
        from ..server import check_bearer
        if DEMO and not env_bool("DEMO_MCP", False):
            return JSONResponse({"error": "MCP is off on this demonstration"}, status_code=404)
        if not check_bearer(request.headers.get("authorization")):
            return JSONResponse({"error": "unauthorized"}, status_code=401,
                                headers={"WWW-Authenticate": "Bearer"})
    resp = await call_next(request)
    if path.startswith("/api/"):
        resp.headers["Cache-Control"] = "no-store"
    return resp


def J(data, status: int = 200) -> JSONResponse:
    return JSONResponse(json.loads(json.dumps(data, default=str)), status_code=status)


@app.get("/healthz", response_class=PlainTextResponse)
def healthz():
    """Liveness of the SERVICE (notes readable, index in sync, reviewer alive) — the
    quality of the content goes to the review, it does not make the service down."""
    problems = []
    mem = service.memory_dir()
    try:
        n_files = len([p for p in mem.glob("*.md") if p.name != "MEMORY.md"])
    except OSError as e:
        problems.append(f"notes unreadable: {e}")
        n_files = -1
    try:
        st = service.get_store().stats()
        if st["generation"] is None:
            if time.time() - STARTED_AT > 900 and n_files > 0:
                problems.append("no index after 15 min")
        elif n_files >= 0 and abs(st["notes"] - n_files) > max(3, n_files // 20):
            problems.append(f"index out of sync ({st['notes']} indexed / {n_files} files)")
    except Exception as e:  # noqa: BLE001
        problems.append(f"database unreachable: {str(e)[:120]}")
    rep = _state["report"]
    every = env_int("REVIEW_EVERY_S", 3600)
    if every > 0 and env_bool("WORKERS", True):
        if rep:
            age = (dt.datetime.now(dt.timezone.utc)
                   - dt.datetime.fromisoformat(rep["at"])).total_seconds()
            if age > 2 * every + 600:
                problems.append(f"review stopped (last one {int(age // 60)} min ago)")
        elif time.time() - STARTED_AT > 900:
            problems.append("no review since start")
    if problems:
        return PlainTextResponse("degraded: " + "; ".join(problems), status_code=503)
    return "ok"


@app.get("/api/info")
def api_info():
    """What the page needs before anything else (no token required)."""
    from ..server import token
    lic = None
    with contextlib.suppress(Exception):
        from .. import models
        st = models.status()
        lic = {"decision": st["licence"].get("decision"),
               "current": st["licence"].get("current"),
               "embed": (st.get("active") or {}).get("embed"),
               "reason": (st.get("active") or {}).get("reason")}
    return J({"version": __version__, "demo": DEMO, "public_read": PUBLIC_READ,
              "writable": not DEMO and len(token()) >= 32,
              "lang": (env("UI_LANG") or "").lower() or None,
              "notify": notify.enabled(), "licence": lic,
              "demo_text": env("DEMO_BANNER") if DEMO else None,
              "title": env("UI_TITLE") or None})


@app.get("/api/status")
def api_status(request: Request):
    require_read(request)
    out = service.status()
    runner = _state.get("runner")
    out["indexer"] = runner.status() if runner else None
    out["review"] = {"running": _state["running"], "at": (_state["report"] or {}).get("at")}
    return J(out)


@app.get("/api/graph")
def api_graph(request: Request, since: str = ""):
    require_read(request)
    g = graph()
    if since and since == g["version"]:
        return J({"version": g["version"], "unchanged": True})
    return J(g)


@app.get("/api/note/{name}")
def api_note(request: Request, name: str):
    require_read(request)
    if "/" in name or ".." in name:
        raise HTTPException(400, "invalid name")
    return J(note(name))


@app.get("/api/search")
def api_search(request: Request, q: str = Query(min_length=2, max_length=500), k: int = 12,
               deep: bool = False):
    require_read(request)
    rows = service.search(q, k, deep=deep)
    err = next((r["error"] for r in rows if "error" in r), None)
    hits = [r for r in rows if "note_name" in r]
    degraded = next((r.get("degraded") for r in hits if r.get("degraded")), None)
    if err and not hits:
        degraded = err
    return J({"query": q, "degraded": degraded, "results": [
        {"note": r["note_name"], "score": r["score"], "cosine": r.get("cosine"),
         "lexical": r.get("lexical"), "rerank": r.get("rerank"), "snippet": r.get("snippet"),
         "age_days": r.get("age_days"), "date_source": r.get("date_source")} for r in hits]})


def overview() -> dict:
    rep = current()
    try:
        h = history()
        series, summary, digest_at = h.series(), h.summary(), h.get("digest_at")
    except Exception:  # noqa: BLE001
        series, summary, digest_at = [], {}, None
    pending = [p for p in proposals() if p["status"] == "pending"]
    return {"report": rep, "history": series, "summary": summary, "pending": len(pending),
            "running": _state["running"], "notify": notify.enabled(), "digest_at": digest_at}


@app.get("/api/review")
def api_review(request: Request):
    require_read(request)
    return J(overview())


@app.post("/api/review/run")
async def api_review_run(request: Request, notify_now: bool = Query(False, alias="notify")):
    require_write(request)
    rep = await asyncio.to_thread(run_review, alert=False)
    sent = None
    if notify_now:
        sent = await asyncio.to_thread(maybe_alert, rep, force=True)
    return J({**overview(), "notified": sent})


@app.get("/api/stats")
def api_stats(request: Request):
    require_read(request)
    return J(stats())


@app.get("/api/proposals")
def api_proposals(request: Request):
    require_read(request)
    return J(proposals())


@app.post("/api/proposals")
def api_propose(request: Request, body: ProposalIn):
    return J(propose(body, require_write(request)))


@app.post("/api/proposals/{pid}/approve")
def api_approve(request: Request, pid: str):
    return J(decide(pid, True, require_write(request)))


@app.post("/api/proposals/{pid}/refuse")
def api_refuse(request: Request, pid: str):
    return J(decide(pid, False, require_write(request)))


@app.post("/api/index/run")
def api_index_run(request: Request):
    require_write(request)
    runner = _state.get("runner")
    if runner is None:
        raise HTTPException(503, "the indexer does not run in this process")
    runner.kick()
    return J({"kicked": True, "indexer": runner.status()})


# --- bench ---------------------------------------------------------------------------

@app.get("/api/bench")
def api_bench(request: Request):
    require_read(request)
    from .. import eval as ev
    try:
        return J(ev.overview(service.get_store()))
    except Exception as e:  # noqa: BLE001
        raise HTTPException(503, f"bench unavailable: {e}") from e


class QuestionIn(BaseModel):
    question: str
    notes: list[str]


@app.post("/api/bench/questions")
def api_bench_add(request: Request, body: QuestionIn):
    user = require_write(request)
    from .. import eval as ev
    try:
        q = ev.add_question(service.get_store(), body.question, body.notes, source="client",
                            author=user)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return J(q.as_dict())


_bench = {"running": False}


@app.post("/api/bench/run")
async def api_bench_run(request: Request):
    require_write(request)
    from .. import embed
    from .. import eval as ev
    if _bench["running"]:
        raise HTTPException(409, "a bench run is in progress")
    _bench["running"] = True
    try:
        st = service.get_store()
        e = service.embedder()
        r = await asyncio.to_thread(ev.run, st, e, profile=embed.current_profile(),
                                    trigger="dashboard")
        reg = await asyncio.to_thread(ev.check_regression, st, r["id"])
    except Exception as e:  # noqa: BLE001
        raise HTTPException(400, str(e)) from e
    finally:
        _bench["running"] = False
    return J({**r, "regression": reg})


# --- recall hook ---------------------------------------------------------------------

_recaller: dict = {"obj": None, "at": 0.0}


def recaller():
    from .. import embed
    from ..recall import RecallConfig, Recaller
    now = time.monotonic()
    r = _recaller["obj"]
    if r is None or now - _recaller["at"] > 10:
        try:
            prof = embed.current_profile()
        except ValueError:
            prof = None
        r = Recaller(service.get_store(), service.embedder(), RecallConfig.from_env(),
                     profile=prof)
        _recaller.update(obj=r, at=now)
    return r


@app.post("/api/recall/hook")
async def api_recall_hook(request: Request, payload: dict = Body(...)):
    from ..recall import enabled, hook_output
    from ..server import check_bearer
    if not check_bearer(request.headers.get("authorization")):
        raise HTTPException(401, "token required")
    if not enabled():
        return J({})
    try:
        out = await asyncio.to_thread(hook_output, payload, recaller())
    except Exception as e:  # noqa: BLE001 — a hook never breaks a turn
        log.warning("recall hook failed: %s", e)
        out = {}
    return J(out)


# --- pages ---------------------------------------------------------------------------

# the MCP route (/mcp) joins the app's own routes: a POST to /mcp must not be redirected
app.router.routes.extend(_mcp_sub.routes)


@app.get("/", response_class=HTMLResponse)
def index():
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    if DEMO:
        html = html.replace("<body>", '<body data-demo="1">', 1)
        html = html.replace('<meta name="robots" content="noindex, nofollow">',
                            '<meta name="robots" content="index, follow">', 1)
    return HTMLResponse(html, headers={"Cache-Control": "no-store"})


app.mount("/", StaticFiles(directory=str(STATIC)), name="static")
