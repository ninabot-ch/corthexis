"""The running memory: one store, the embedder that serves its active index, and the
read API shared by the MCP server, the CLI, the recall hook endpoint and the dashboard.

Queries are embedded by the model the active index generation was built with
(``switch.serving_embedder``); when no configured server embeds with that model (a model
change is being indexed), searches degrade to keywords only instead of comparing vectors
of two different models.

Reranking (``embed.rerank_policy()``): ``interactive`` (GPU profile) → top 10 of every
search; ``async`` (standard profile) → only for a deep search asked explicitly; ``off``
(light profile) → never. ``CORTHEXIS_SEARCH_RERANK=1|0`` forces it on or off.

Projects and levels (2.1): the notes of a memory may be split into projects, each note
at one of five levels (``corthexis.scope``, ``corthexis.levels``). ``CORTHEXIS_RECALL_PROJECTS``
(``radio,shared@1``…) is the scope of this process: what the MCP tools, the CLI and the
hook endpoint may read. Unset = no scope: every note, as in 2.0 (a standalone memory is one
project, ``default``, every note at the default level).
"""
from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Callable

from . import scope as _scope
from .config import env

_lock = threading.Lock()
_store = None
_qemb: tuple[float, int | None, object] | None = None   # (checked at, generation, embedder)


def memory_dir() -> Path:
    """``CORTHEXIS_MEMORY_DIR`` (``CORTHEXIS_NOTES_DIR``, the 1.x name, is read too)."""
    return Path(env("MEMORY_DIR") or "~/.corthexis/memory").expanduser()


def data_dir() -> Path:
    """Where the service keeps its own state (repair proposals, backups)."""
    return Path(env("DATA_DIR") or "~/.local/share/corthexis").expanduser()


def scope(projects=None) -> tuple[str, ...] | None:
    """The project scope of a call: ``projects`` when given, else the process's
    ``CORTHEXIS_RECALL_PROJECTS``; ``None`` = no scope (everything is readable)."""
    if projects is not None:
        return _scope.normalize(projects)
    return _scope.from_env(env("RECALL_PROJECTS"))


def narrow(scope, within):
    """``scope`` limited to ``within``: only its projects, never above its clearances
    (a client-sent scope can be narrower than the service's, never wider)."""
    if within is None:
        return scope
    if scope is None:
        return within
    allowed, want = _scope.caps(within), _scope.caps(scope)
    return tuple(sorted(_scope.entry(p, min(c, allowed[p])) for p, c in want.items()
                        if p in allowed))


def get_store():
    """One Store (connection pool) per process, opened on first use."""
    global _store
    if _store is None:
        with _lock:
            if _store is None:
                from .store import SearchConfig, Store
                _store = Store(config=SearchConfig.from_env(),
                               max_size=int(env("DB_POOL", "4") or 4))
    return _store


def reset() -> None:
    """Forget the store and the query embedder (tests, configuration change)."""
    global _store, _qemb
    with _lock:
        if _store is not None:
            try:
                _store.close()
            except Exception:  # noqa: BLE001
                pass
        _store, _qemb = None, None


class NoServingEmbedder:
    """Stand-in when no embedder matches the active generation (model change pending):
    queries fail → keyword-only search, flagged — never vectors of another model."""
    backend = "none"
    rerank_policy = "off"
    rerank_url = None
    lexical_weight = None

    def __init__(self, identity: str):
        self._identity = identity

    def identity(self) -> str:
        return f"none (index built with {self._identity})"

    def embed_query(self, text: str, timeout: float = 30.0):
        raise RuntimeError(f"no embedding server serves the active index ({self._identity})")

    def rerank(self, query, docs, timeout: float = 3.0):
        return None


def serving_embedder(store=None):
    """Embedder whose identity matches the active generation; None = no generation yet."""
    from . import embed, switch

    store = store or get_store()
    g = store.active_generation()
    if g is None:
        return None
    e = switch.serving_embedder(store)
    if e is not None:
        return e
    leg = embed.legacy()
    if g.embed_identity in (leg.identity(), leg.identity_2x()):
        return leg
    return NoServingEmbedder(g.embed_identity)


def embedder():
    """Query embedder, re-checked every 10 s: the indexer may activate a generation built
    with another model in another thread or process."""
    global _qemb
    from . import embed

    now = time.monotonic()
    if _qemb is not None and now - _qemb[0] < 10:
        return _qemb[2]
    gen = None
    try:
        st = get_store()
        g = st.active_generation()
        gen = g.id if g else None
        e = serving_embedder(st) or embed.get()
    except Exception:  # noqa: BLE001 — store down: the search reports it
        e = embed.get()
    _qemb = (now, gen, e)
    return e


def rerank_policy() -> str:
    try:
        return getattr(embedder(), "rerank_policy", "off") or "off"
    except Exception:  # noqa: BLE001
        return "off"


def _reranker(deep: bool = False) -> Callable | None:
    forced = (env("SEARCH_RERANK") or "").lower()
    if forced in ("0", "false", "no", "off"):
        return None
    policy = rerank_policy()
    if forced in ("1", "true", "yes", "on") or policy == "interactive" or (
            deep and policy == "async"):
        e = embedder()
        timeout = 3.0 if policy == "interactive" else 15.0
        return lambda q, docs: e.rerank(q, docs, timeout=timeout)
    return None


def search(query: str, top_k: int = 8, *, deep: bool = False,
           projects=None) -> list[dict]:
    """Ranked notes for ``query`` (``memory_search``). Never raises: an error is a row
    ``{"error": …}``, a missing model degrades to keywords with ``degraded`` set.
    ``projects`` = the caller's scope (see ``scope``); an empty scope finds nothing."""
    from .search import tokens
    from .store import DimensionMismatch, StoreError

    query = (query or "").strip()
    if not query:
        return [{"error": "empty query"}]
    sc = scope(projects)
    if sc == ():
        return [{"info": "No note is readable in this scope.", "empty": True}]
    scoped = {"projects": sc} if sc is not None else {}
    try:
        st = get_store()
    except Exception as e:  # noqa: BLE001 — database down
        return [{"error": f"memory store unavailable: {e}"}]
    degraded = None
    try:
        q = embedder().embed_query(query)
    except Exception as e:  # noqa: BLE001 — degrade to keyword-only instead of failing
        q = None
        degraded = f"embedding server unavailable — keyword-only ranking ({e})"
    if q is None and not tokens(query):
        return [{"error": f"{degraded}; the query has no usable keyword"}]
    top_k = max(1, min(int(top_k or 8), 50))
    try:
        hits = st.search(q, query, top_k, rerank=_reranker(deep) if q is not None else None,
                         **scoped)
    except DimensionMismatch as e:
        degraded = f"query embedding does not match the index ({e}) — keyword-only ranking"
        if not tokens(query):
            return [{"error": degraded}]
        hits = st.search(None, query, top_k, **scoped)
    except StoreError as e:
        return [{"error": str(e)}]
    hits = _scope.filter_hits(hits, sc)   # the rule holds even for a store that ignores it
    if not hits:
        return [{"info": "No memory yet: the index is empty or has no active generation.",
                 "empty": True}]
    out = []
    for h in hits:
        d = h.as_dict()
        if degraded:
            d["degraded"] = degraded
        out.append(d)
    return out


def age_header(name: str, modified, source) -> str:
    """``[note x — updated 2026-09-01, 32 d ago]`` (+ provenance when reconstructed)."""
    from .contract import MEASURED_SOURCES
    from .search import age

    mod, days, src = age(modified, source)
    if days is None:
        return f"[note {name} — last update date unknown]"
    head = f"[note {name} — updated {mod}, {days} d ago"
    if src not in MEASURED_SOURCES:
        head += f"; reconstructed date ({src}), exact day not guaranteed"
    return head + "]"


def get_record(note_name: str, *, projects=None):
    """The ``NoteRecord`` a caller with this scope may read under ``note_name`` (name or
    file name): its own project first, then ``shared``; None = no such note *for this
    caller* (a note of another project, or above the clearance, does not exist)."""
    st = get_store()
    name = (note_name or "").strip().removesuffix(".md")
    if not name:
        return None
    sc = scope(projects)
    if sc == ():
        return None
    note = st.resolve_note(name, sc)
    if note is None:
        by_path = st.find_note_by_path(name, sc)
        note = st.resolve_note(by_path, sc) if by_path else None
    if note is not None and not _scope.visible(note, sc):
        return None
    return note


def get(note_name: str, *, projects=None, via: str | None = None,
        actor: str | None = None, session_id: str | None = None) -> str | None:
    """Full body of a note prefixed with its age and date provenance (``memory_get``);
    None = no such note. Accepts the note name or its file name. With a scope and ``via``
    set, the read is written to the access log (the audited recall)."""
    note = get_record(note_name, projects=projects)
    if note is None:
        return None
    if via and scope(projects) is not None and hasattr(get_store(), "log_access"):
        try:
            get_store().log_access(via, [note], actor=actor, session_id=session_id)
        except Exception:  # noqa: BLE001 — the audit never blocks a read
            pass
    return age_header(note.name, note.modified, note.modified_source) + "\n\n" + (note.body or "")


def links(note_name: str, *, projects=None) -> dict:
    st = get_store()
    note = get_record(note_name, projects=projects)
    if note is None:
        return {"error": f"note not found: {note_name}"}
    out = st.links(note.name, _scope.project_of(note))
    out["links"] = [{"name": r["name"], "description": r["description"] or "",
                     "exists": bool(r["exists"])} for r in out["links"]]
    out["backlinks"] = [{"name": r["name"], "description": r["description"] or ""}
                        for r in out["backlinks"]]
    return out


def status() -> dict:
    """What runs: store, active generation, embedding model and servers, licence."""
    from . import embed, models

    out: dict = {"memory_dir": str(memory_dir())}
    try:
        st = get_store()
        g = st.active_generation()
        out["store"] = {"ok": True, **st.stats()}
        out["generation"] = None if g is None else {
            "id": g.id, "embed_identity": g.embed_identity, "dim": g.dim,
            "created_at": str(g.created_at)}
        out["building"] = [{"id": x.id, "embed_identity": x.embed_identity}
                           for x in st.generations() if x.status == "building"]
        e = embedder()
        out["serving"] = e.identity() if hasattr(e, "identity") else None
    except Exception as e:  # noqa: BLE001
        out["store"] = {"ok": False, "error": str(e)[:300]}
    try:
        out["embed"] = embed.describe()
        out["embed"]["health"] = embed.get().health(timeout=1.0)
    except Exception as e:  # noqa: BLE001
        out["embed"] = {"error": str(e)[:300]}
    try:
        st_m = models.status()
        lic = st_m["licence"]
        out["models"] = {"active": st_m["active"], "terms_version": st_m["terms_version"],
                         "licence": {k: lic.get(k) for k in
                                     ("decision", "current", "terms_version", "at", "via")}}
    except Exception as e:  # noqa: BLE001
        out["models"] = {"error": str(e)[:300]}
    return out
