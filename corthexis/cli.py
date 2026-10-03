"""``corthexis`` — command line of the memory engine.

    corthexis serve                    dashboard + MCP over HTTP + indexer + reviewer (one process)
    corthexis mcp                      MCP server over stdio (started by the agent)
    corthexis index [--watch] [--rebuild] [--no-normalize]
    corthexis search "question" [-k 8] [--json]
    corthexis get NOTE
    corthexis status [--json]
    corthexis review [--chain]         exit 1 on a critical finding (JSON report on stdout)
    corthexis eval run|harvest|add|list|report|compare …
    corthexis normalize [--dry-run]    apply the naming convention (show the plan only)
    corthexis migrate status|run|approve …   from a SQLite index (CortHeXis 1.x, SOKKAN 2.x)
    corthexis models setup|status|terms …    licence decision and model download
    corthexis profile                  recommended memory profile for this machine
    corthexis hook install|uninstall|show|run   recall hook for Claude Code
    corthexis notify-test              send a test message to the configured channels

Configuration is read from ``CORTHEXIS_*`` variables (see ``.env.example``).
"""
from __future__ import annotations

import json
import sys

from . import __version__

USAGE = __doc__


def _search(argv: list[str]) -> int:
    import argparse

    from . import service
    ap = argparse.ArgumentParser(prog="corthexis search")
    ap.add_argument("query", nargs="+")
    ap.add_argument("-k", "--top-k", type=int, default=8)
    ap.add_argument("--deep", action="store_true", help="rerank even on the standard profile")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    rows = service.search(" ".join(a.query), a.top_k, deep=a.deep)
    if a.json:
        print(json.dumps(rows, indent=1, ensure_ascii=False, default=str))
        return 0
    for r in rows:
        if "error" in r or "info" in r:
            print(r.get("error") or r.get("info"), file=sys.stderr)
            return 1 if "error" in r else 0
        age = f"{r['age_days']} d" if r.get("age_days") is not None else "undated"
        print(f"{r['score']:.3f}  {r['note_name']}  ({age})")
        if r.get("description"):
            print(f"       {r['description'][:150]}")
    deg = next((r["degraded"] for r in rows if r.get("degraded")), None)
    if deg:
        print(f"! {deg}", file=sys.stderr)
    return 0


def _get(argv: list[str]) -> int:
    from . import service
    if len(argv) != 1:
        print("usage: corthexis get NOTE", file=sys.stderr)
        return 2
    body = service.get(argv[0])
    if body is None:
        print(f"note not found: {argv[0]}", file=sys.stderr)
        return 1
    print(body)
    return 0


def _status(argv: list[str]) -> int:
    from . import service
    st = service.status()
    if "--json" in argv:
        print(json.dumps(st, indent=1, ensure_ascii=False, default=str))
        return 0 if st.get("store", {}).get("ok") else 1
    store, gen, e = st.get("store", {}), st.get("generation"), st.get("embed", {})
    lic = (st.get("models") or {}).get("licence") or {}
    print(f"CortHeXis {__version__}")
    print(f"notes folder   {st['memory_dir']}")
    if store.get("ok"):
        print(f"store          {store.get('notes')} notes, {store.get('chunks')} passages")
    else:
        print(f"store          UNREACHABLE — {store.get('error')}")
    print("index          " + (f"generation {gen['id']} ({gen['embed_identity']})" if gen
                                 else "none yet (run: corthexis index)"))
    for b in st.get("building") or []:
        print(f"               building generation {b['id']} ({b['embed_identity']})")
    if st.get("serving") and gen and st["serving"] != gen["embed_identity"]:
        print(f"queries        {st['serving']} — keyword-only until the index matches")
    if "error" in e:
        print(f"embedding      {e['error']}")
    else:
        print(f"embedding      {e.get('label') or e.get('model')} · profile {e.get('profile')}")
        for h in (e.get("health") or {}).get("embed") or []:
            print(f"               {'up  ' if h['ok'] else 'DOWN'} {h['url']}")
        rr = (e.get("health") or {}).get("rerank")
        if rr:
            print(f"reranker       {'up  ' if rr['ok'] else 'DOWN'} {rr['url']}")
    dec = lic.get("decision") or "undecided"
    print(f"gemma terms    {dec}" + ("" if lic.get("current") or not lic.get("decision")
                                     else " (for an older version of the terms)"))
    return 0 if store.get("ok") else 1


def _serve(argv: list[str]) -> int:
    import argparse
    import os

    import uvicorn
    ap = argparse.ArgumentParser(prog="corthexis serve")
    ap.add_argument("--host", default=os.environ.get("CORTHEXIS_HOST", "127.0.0.1"))
    ap.add_argument("--port", type=int, default=int(os.environ.get("CORTHEXIS_PORT", "8420")))
    a = ap.parse_args(argv)
    uvicorn.run("corthexis.dashboard.app:app", host=a.host, port=a.port, log_level="info",
                proxy_headers=True, access_log=False)
    return 0


def _notify_test(argv: list[str]) -> int:
    from . import notify
    if not notify.enabled():
        print("no channel: set CORTHEXIS_NOTIFY_WEBHOOKS or CORTHEXIS_NOTIFY_CMD", file=sys.stderr)
        return 1
    out = notify.send("CortHeXis — test", "If you read this, the review can reach you.",
                      notify.public_url("/"), "test")
    for k, v in out.items():
        print(f"{k}: {v}")
    return 0 if all(v == "ok" for v in out.values()) else 1


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ("-h", "--help", "help"):
        print(USAGE)
        return 0
    if argv[0] in ("-V", "--version", "version"):
        print(__version__)
        return 0
    cmd, rest = argv[0], argv[1:]
    if cmd == "serve":
        return _serve(rest)
    if cmd == "mcp":
        from .server import main as m
        return m(rest)
    if cmd == "index":
        from .indexer import main as m
        return m(rest)
    if cmd == "search":
        return _search(rest)
    if cmd == "get":
        return _get(rest)
    if cmd == "status":
        return _status(rest)
    if cmd == "review":
        from .review import main as m
        return m(rest)
    if cmd == "eval":
        from .eval import main as m
        return m(rest)
    if cmd == "normalize":
        from .normalize import main as m
        return m(rest)
    if cmd == "migrate":
        from .migrate import main as m
        return m(rest)
    if cmd == "models":
        from .models import main as m
        return m(rest)
    if cmd == "profile":
        from .profiles import main as m
        return m(rest)
    if cmd == "hook":
        from .hook import main as m
        return m(rest)
    if cmd == "notify-test":
        return _notify_test(rest)
    print(f"unknown command {cmd!r}\n{USAGE}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
