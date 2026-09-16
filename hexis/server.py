#!/usr/bin/env python3
"""MCP server exposing semantic recall over the indexed notes.

Two tools:

``memory_search(query, top_k)``
    Ranked notes with a score in [0..1] and an excerpt. The score blends dense
    cosine similarity (cross-lingual) with lexical overlap of the query terms,
    so an exact keyword match is not drowned out by a merely-related note.

``memory_get(note_name)``
    The full body of one note.

Call ``memory_search`` at the start of a task instead of loading a whole index
into context. If the embedding backend is unreachable the server degrades to
lexical-only scoring and says so in the result, rather than silently returning
worse answers.

    python -m hexis.server
"""
from __future__ import annotations

import datetime
import json
import os
import sqlite3
from pathlib import Path

try:  # mcp >= 2.0 renamed FastMCP to MCPServer
    from mcp.server.mcpserver import MCPServer as _Server
except ImportError:  # mcp 1.x
    from mcp.server.fastmcp import FastMCP as _Server

from .embeddings import describe as embed_describe
from .embeddings import embed_one

DB_PATH = Path(
    os.environ.get("HEXIS_DB", "~/.local/share/hexis/memory.db")
).expanduser()

mcp = _Server("hexis")


def _embed_query(text: str) -> list[float]:
    return embed_one(text)


def _load_chunks() -> list[tuple]:
    """(note_name, description, source_path, modified, modified_source, body, embedding)."""
    if not DB_PATH.exists():
        return []
    con = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    try:
        rows = con.execute(
            "SELECT c.note_name, n.description, n.source_path, n.modified, "
            "n.modified_source, c.body, c.embedding "
            "FROM chunks c JOIN notes n ON n.name = c.note_name"
        ).fetchall()
    finally:
        con.close()
    return [(r[0], r[1], r[2], r[3], r[4], r[5], json.loads(r[6])) for r in rows]


def _age(modified: str | None, source: str | None) -> dict:
    """Champs d'âge joints à chaque résultat.

    Without it, a note from months ago and a note from yesterday come back at
    rien ne distingue les deux à la lecture — c'est comme ça qu'une alerte quota
    the same rank, and a stale note gets served as though it were fresh.
    `date_source` != "frontmatter" signale une date RECONSTRUITE, pas mesurée
    (cf. backfill_modified.py) : ordre de grandeur fiable, jour exact non.
    """
    if not modified:
        return {"modified": None, "age_days": None, "date_source": "inconnue"}
    try:
        d = datetime.datetime.fromisoformat(str(modified).replace("Z", "+00:00"))
    except ValueError:
        return {"modified": str(modified), "age_days": None, "date_source": source or "inconnue"}
    if d.tzinfo is None:
        d = d.replace(tzinfo=datetime.timezone.utc)
    age = (datetime.datetime.now(datetime.timezone.utc) - d).days
    return {
        "modified": d.date().isoformat(),
        "age_days": age,
        "date_source": source or "frontmatter",
    }


# weight of the lexical (keyword-overlap) signal in the final blend; the dense
# cosine carries the rest. Tuned so exact jargon hits ("promo", "jobup") surface
# without drowning the semantic signal on keyword-free queries.
LEXICAL_WEIGHT = 0.25
_STOP = {
    "les", "des", "sur", "de", "la", "le", "du", "un", "une", "pour", "dans",
    "avec", "et", "the", "to", "and", "of", "on", "in", "how", "que", "qui",
}


def _tokens(text: str) -> set[str]:
    out: set[str] = set()
    cur = ""
    for ch in text.lower():
        if ch.isalnum():
            cur += ch
        else:
            if len(cur) >= 3 and cur not in _STOP:
                out.add(cur)
            cur = ""
    if len(cur) >= 3 and cur not in _STOP:
        out.add(cur)
    return out


@mcp.tool()
def memory_search(query: str, top_k: int = 8) -> list[dict]:
    """Semantic search over the memory notes.

    Returns the most relevant notes, each with a score in [0..1] and an
    excerpt. The score blends dense cosine similarity (cross-lingual) with
    lexical overlap of the query terms. Call this at the START of a task on a
    subject that may already have been covered, instead of loading the whole
    MEMORY.md index into context.

    Args:
        query: the question or work subject, in any language.
        top_k: how many notes to return (default 8).
    """
    chunks = _load_chunks()
    if not chunks:
        return [{"error": "index empty or missing — run `python -m hexis.index`", "db": str(DB_PATH)}]
    qtok = _tokens(query)
    degraded = None
    try:
        q = _embed_query(query)
    except Exception as e:  # noqa: BLE001 — degrade to lexical-only instead of failing
        if not qtok:
            return [{"error": f"embedding backend unavailable ({embed_describe()}) and the query has no usable keyword: {e}"}]
        q = None
        degraded = f"embedding backend unavailable ({embed_describe()}) — lexical scoring only, degraded recall (no cross-lingual matching)"
    # aggregate per note: best chunk (for snippet) + full-note lexical haystack;
    # chunk relevance = cosine, or keyword overlap in degraded lexical-only mode
    agg: dict[str, dict] = {}
    for note_name, description, source_path, modified, msource, body, emb in chunks:
        if q is not None:
            rel = sum(a * b for a, b in zip(q, emb))
        else:
            rel = len(qtok & _tokens(body)) / len(qtok)
        a = agg.get(note_name)
        if a is None:
            a = agg[note_name] = {
                "note_name": note_name,
                "description": description,
                "path": Path(source_path).name,
                "age": _age(modified, msource),
                "rel": rel,
                "snippet": body,
                "hay": f"{note_name} {description} {body}",
            }
        else:
            a["hay"] += " " + body
            if rel > a["rel"]:
                a["rel"], a["snippet"] = rel, body

    results = []
    for a in agg.values():
        lex = (len(qtok & _tokens(a["hay"])) / len(qtok)) if qtok else 0.0
        if q is None:
            # note-wide overlap + chunk concentration (breaks ties between notes
            # that all contain every keyword somewhere)
            score = 0.7 * lex + 0.3 * a["rel"]
        else:
            score = (1 - LEXICAL_WEIGHT) * a["rel"] + LEXICAL_WEIGHT * lex
        snippet = a["snippet"] if len(a["snippet"]) <= 320 else a["snippet"][:317] + "…"
        results.append({
            "note_name": a["note_name"],
            **a["age"],
            "description": a["description"],
            "score": round(score, 4),
            "cosine": round(a["rel"], 4) if q is not None else None,
            "snippet": snippet,
            "path": a["path"],
            **({"degraded": degraded} if degraded else {}),
        })
    results.sort(key=lambda d: d["score"], reverse=True)
    return results[: max(1, top_k)]


@mcp.tool()
def memory_get(note_name: str) -> str:
    """Return the full body of one memory note, by name (without .md)."""
    if not DB_PATH.exists():
        return f"index not found: {DB_PATH}"
    con = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    try:
        rows = con.execute(
            "SELECT body FROM chunks WHERE note_name = ? ORDER BY chunk_idx",
            (note_name,),
        ).fetchall()
        meta = con.execute(
            "SELECT modified, modified_source FROM notes WHERE name = ?", (note_name,)
        ).fetchone()
        if not rows:
            # fallback: resolve by filename stem (frontmatter name may differ).
            # Files are often named with "_" and notes with "-": without this
            # second lookup, any note whose `name:` differs from its filename
            # filename could not be resolved (frontmatter name may differ).
            stem = note_name.removesuffix(".md")
            for like in (f"%/{stem}.md", f"%/{stem.replace('-', '_')}.md"):
                rows = con.execute(
                    "SELECT c.body FROM chunks c JOIN notes n ON n.name = c.note_name "
                    "WHERE n.source_path LIKE ? ORDER BY c.chunk_idx",
                    (like,),
                ).fetchall()
                if rows:
                    meta = con.execute(
                        "SELECT modified, modified_source FROM notes WHERE source_path LIKE ?",
                        (like,),
                    ).fetchone()
                    break
    finally:
        con.close()
    if not rows:
        return f"note not found: {note_name}"
    # L'âge est en tête du corps, pas seulement dans memory_search : une note
    # lue directement doit dire elle-même de quand elle date.
    age = _age(*(meta or (None, None)))
    if age["age_days"] is None:
        head = f"[note {note_name} — last-updated date unknown]"
    else:
        head = f"[note {note_name} — updated {age['modified']}, {age['age_days']}d ago"
        if age["date_source"] != "frontmatter":
            head += f"; date inferred ({age['date_source']}), exact day not guaranteed"
        head += "]"
    return head + "\n\n" + "\n\n".join(r[0] for r in rows)


if __name__ == "__main__":
    mcp.run()
