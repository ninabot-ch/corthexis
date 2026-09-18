#!/usr/bin/env python3
"""Index a folder of memory notes for semantic recall.

Reads every note (``$CORTHEXIS_NOTES_DIR/*.md``, excluding the generated
``MEMORY.md`` index), splits each into chunks, embeds them (see
``corthexis.embeddings`` — multilingual by default, so notes written in one
language are recalled by queries in another) and stores unit-normalized
vectors in a local SQLite database. The MCP server (``corthexis.server``) then
does cosine = dot-product top-k at query time.

Incremental: a note is re-embedded only when its file changes; notes whose
files disappeared are pruned. A few hundred notes index in seconds.

    python -m corthexis.index            # incremental
    python -m corthexis.index --rebuild  # drop and rebuild every vector

Run it on a timer, or on a filesystem watch (see ``systemd/``).
"""
from __future__ import annotations

import argparse
import datetime
import json
import math
import os
import re
import sqlite3
import sys
from pathlib import Path

import yaml

from .embeddings import describe as embed_describe
from .embeddings import embed, normalize

MEMORY_DIR = Path(os.environ.get("CORTHEXIS_NOTES_DIR", "./notes")).expanduser()
DB_PATH = Path(
    os.environ.get("CORTHEXIS_DB", "~/.local/share/corthexis/memory.db")
).expanduser()
CHUNK_TARGET = 1200  # chars; notes longer than this are split on paragraph boundaries
# MEMORY.md is loaded whole into every session's context; the harness truncates
# past ~24.4KB, so the generated index must stay under budget (UTF-8 bytes).
INDEX_BUDGET = 24_000
# ... et il n'en charge que les INDEX_MAX_LINES premières lignes (constaté : 200).
INDEX_MAX_LINES = 190
# Fenêtre « touchées récemment » de l'index (jours).
RECENT_DAYS = 21

FRONTMATTER_RE = re.compile(r"^---[ \t]*\n(.*?)\n---[ \t]*\n", re.S | re.M)


def _line_parse_frontmatter(raw: str) -> dict:
    """Repli ligne-à-ligne quand YAML refuse le bloc.

    Observed in the wild: an unquoted `description:` containing a colon makes
    the whole frontmatter block fail to parse, and the note silently loses both
    its name AND its description — degraded recall, blank index line, no error.
    """
    fm: dict = {}
    meta: dict = {}
    in_meta = False
    for line in raw.split("\n"):
        if not line.strip():
            continue
        if in_meta and re.match(r"^\s+\S", line):
            k, _, v = line.strip().partition(":")
            meta[k.strip()] = v.strip().strip("\"'")
            continue
        k, _, v = line.partition(":")
        k = k.strip()
        if k == "metadata":
            in_meta = True
            continue
        in_meta = False
        fm[k] = v.strip().strip("\"'")
    if meta:
        fm["metadata"] = meta
    return fm


def parse_note(path: Path) -> dict:
    """Return {name, description, type, body} for a memory note.

    Tolérant par construction : le frontmatter est cherché n'importe où en tête
    (des notes ont déjà reçu une « MAJ » collée AU-DESSUS du bloc), YAML cassé
    -> repli ligne-à-ligne, et tout écart est signalé sur stderr plutôt que
    silencieusement avalé.
    """
    text = path.read_text(encoding="utf-8")
    name = path.stem.replace("_", "-")
    description = ""
    ntype = "unknown"
    modified = msource = None
    body = text
    warn = ""

    m = FRONTMATTER_RE.search(text)
    if not m:
        warn = "aucun frontmatter"
    else:
        above = text[: m.start()].strip()
        body = text[m.end():].lstrip("\n")
        if above:
            warn = "contenu écrit au-dessus du frontmatter"
            body = above + "\n\n" + body
        try:
            fm = yaml.safe_load(m.group(1)) or {}
        except yaml.YAMLError as exc:
            fm = _line_parse_frontmatter(m.group(1))
            warn = f"YAML invalide ({str(exc).splitlines()[0]}) — repli ligne-à-ligne"
        name = (fm.get("name") or "").strip() or name
        description = (fm.get("description") or "").strip()
        meta = fm.get("metadata", {}) or {}
        if isinstance(meta, dict):
            ntype = meta.get("type", meta.get("node_type", "unknown"))
            raw = meta.get("modified")
            if isinstance(raw, (datetime.datetime, datetime.date)):
                modified = raw.isoformat()
            elif raw:
                modified = str(raw).strip()
            msource = (str(meta.get("modified_source") or "").strip()) or None
        if not description and not warn:
            warn = "description vide"

    if warn:
        print(f"WARN  {path.name}: {warn}", file=sys.stderr)
    return {
        "name": name,
        "description": description,
        "type": ntype,
        "modified": modified,
        "modified_source": msource,
        "body": body.strip(),
    }


def chunk_body(body: str) -> list[str]:
    """Split a note body into ~CHUNK_TARGET-char chunks on paragraph boundaries."""
    if len(body) <= CHUNK_TARGET:
        return [body] if body else []
    paras = [p.strip() for p in body.split("\n\n") if p.strip()]
    chunks: list[str] = []
    cur = ""
    for p in paras:
        if cur and len(cur) + len(p) + 2 > CHUNK_TARGET:
            chunks.append(cur)
            cur = p
        else:
            cur = f"{cur}\n\n{p}" if cur else p
    if cur:
        chunks.append(cur)
    return chunks


def embed_text(name: str, description: str, chunk: str) -> str:
    """Prepend name + description: those keywords measurably lift recall."""
    return f"{name}. {description}. {chunk}"


def embed_batch(texts: list[str]) -> list[list[float]]:
    """Embed via the configured backend (see ``CORTHEXIS_EMBED_BACKEND``)."""
    return embed(texts)


def init_db(con: sqlite3.Connection) -> None:
    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS notes (
            name TEXT PRIMARY KEY,
            description TEXT,
            type TEXT,
            mtime REAL,
            source_path TEXT
        );
        CREATE TABLE IF NOT EXISTS chunks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            note_name TEXT NOT NULL REFERENCES notes(name) ON DELETE CASCADE,
            chunk_idx INTEGER NOT NULL,
            body TEXT NOT NULL,
            embedding TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_chunks_note ON chunks(note_name);
        CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
        -- Quand le contenu d'une note a été vu pour la première fois sous sa
        -- forme actuelle. Filet sous `metadata.modified` : une note éditée sans
        -- que le champ soit bumpé garderait sinon éternellement sa vieille date
        -- (c'est comme ça qu'une alerte quota de mai a été resservie en
        -- septembre). Volontairement PAS vidée par --rebuild : c'est justement
        -- l'historique qu'un rebuild ne doit pas perdre.
        CREATE TABLE IF NOT EXISTS note_seen (
            name TEXT PRIMARY KEY,
            content_hash TEXT NOT NULL,
            first_seen TEXT NOT NULL,
            -- 1 = contenu simplement découvert au premier passage, jamais vu
            -- changer. Pour une note sans date déclarée, `first_seen` ne vaut
            -- alors rien : elle est « d'âge inconnu », surtout pas « du jour ».
            seeded INTEGER NOT NULL DEFAULT 0
        );
        """
    )
    # Added later: a note's last-updated date must
    # voyager jusqu'au RAG, sinon une note de mai ressort au même rang qu'une
    # note d'hier et rien ne le signale à la lecture.
    have = {r[1] for r in con.execute("PRAGMA table_info(notes)")}
    for col in ("modified", "modified_source"):
        if col not in have:
            con.execute(f"ALTER TABLE notes ADD COLUMN {col} TEXT")
    con.commit()


def effective_modified(con: sqlite3.Connection, note: dict, body: str) -> tuple[str, str]:
    """Date de dernière mise à jour retenue pour une note, + sa provenance.

    `metadata.modified` fait foi tant qu'il n'est pas en retard sur le contenu :
    dès que le corps change, la date de première vue de ce contenu prend le
    dessus. Une note peut donc rajeunir toute seule, jamais vieillir à tort, et
    aucun fichier n'est réécrit par l'indexeur.
    """
    import hashlib

    h = hashlib.sha256(body.encode("utf-8")).hexdigest()
    now = datetime.datetime.now(datetime.timezone.utc)
    declared = None
    if note.get("modified"):
        declared = _parse_dt(note["modified"])

    row = con.execute(
        "SELECT content_hash, first_seen, seeded FROM note_seen WHERE name = ?", (note["name"],)
    ).fetchone()
    if row is None:
        # Amorçage : on ne prétend pas qu'une note connue depuis mai date
        # d'aujourd'hui juste parce que l'index la découvre maintenant.
        first_seen, seeded = (declared or now), 1
    elif row[0] != h:
        first_seen, seeded = now, 0
    else:
        first_seen, seeded = (_parse_dt(row[1]) or now), row[2]

    con.execute(
        "INSERT INTO note_seen(name, content_hash, first_seen, seeded) VALUES(?,?,?,?) "
        "ON CONFLICT(name) DO UPDATE SET content_hash=excluded.content_hash, "
        "first_seen=excluded.first_seen, seeded=excluded.seeded",
        (note["name"], h, first_seen.isoformat(), seeded),
    )

    if declared and declared > first_seen:
        # The date moved since bootstrap: someone (or the agent harness, which
        # tamponne `modified` à chaque écriture sans toucher à
        # `modified_source`) l'a réécrite pour de bon. L'étiquette de
        # reconstruction qui traîne dans le fichier est périmée, on l'ignore —
        # sans ça une date fraîche se présenterait comme approximative.
        return declared.isoformat(), "frontmatter"
    if declared and declared == first_seen:
        return declared.isoformat(), (note.get("modified_source") or "frontmatter")
    if seeded:
        # Ni date déclarée, ni changement observé : on ne sait pas. Mieux vaut
        # l'annoncer que de la faire passer pour fraîche.
        return None, "inconnue"
    return first_seen.isoformat(), "indexed"


def _parse_dt(raw) -> "datetime.datetime | None":
    if isinstance(raw, datetime.datetime):
        return raw if raw.tzinfo else raw.replace(tzinfo=datetime.timezone.utc)
    try:
        d = datetime.datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    return d if d.tzinfo else d.replace(tzinfo=datetime.timezone.utc)


def _truncate(text: str, cap: int) -> str:
    if len(text) <= cap:
        return text
    cut = text[: cap - 1]
    sp = cut.rfind(" ")
    if sp > cap // 2:
        cut = cut[:sp]
    return cut.rstrip(" ,;:—-") + "…"


def write_memory_index(con: sqlite3.Connection) -> None:
    """Regenerate MEMORY.md from the notes' frontmatter descriptions.

    Two ceilings, not one: the agent harness truncates MEMORY.md at
    INDEX_MAX_LINES lines **and** ~INDEX_BUDGET bytes. Past ~200 notes it is the
    line count that bites first, and one line per note made the tail of the
    boot-loaded index simply vanish — with no signal whatsoever. Hence three
    tiers:
      1. `feedback`/`user` notes — the working rules, the ones that must be read
         without having to be looked up — with their hook;
      2. recently touched notes (`metadata.modified`), with their hook;
      3. EVERY note grouped by family, as bare links: the overview is
         exhaustive, so no note can drop out of the index in silence.
    The detail is read through memory_search / memory_get anyway.
    """
    rows = con.execute(
        "SELECT name, description, source_path, type FROM notes ORDER BY name COLLATE NOCASE"
    ).fetchall()
    # La date vient de la base (cf. effective_modified), plus du mtime ni d'une
    # relecture du fichier : une réécriture mécanique du corpus remet tous les
    # mtime to the same second, which is exactly how dates get lost.
    mod_map = dict(con.execute("SELECT name, modified FROM notes").fetchall())
    header = (
        "<!-- Generated by `python -m corthexis.index` from the notes' frontmatter "
        "descriptions — do not edit by hand. This is an overview only: read the "
        "content with memory_search / memory_get. -->\n\n"
    )

    recent_cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=RECENT_DAYS)
    rules, dated = [], []
    for row in rows:
        name, _desc, source_path, ntype = row
        if ntype in ("feedback", "user"):
            rules.append(row)
            continue
        mod = _parse_dt(mod_map.get(name))
        if mod and mod >= recent_cutoff:
            dated.append((mod, row))
    # Most recent first: if the window does not fit the budget, it is
    # la queue de liste — les moins fraîches — qui saute, jamais le haut.
    dated.sort(key=lambda t: t[0], reverse=True)
    recent_all = [row for _m, row in dated]

    families: dict[str, list[tuple[str, str]]] = {}
    for name, _desc, source_path, _t in rows:
        families.setdefault(name.split("-")[0].split("_")[0], []).append(
            (name, Path(source_path).name)
        )
    # Le lien n'est écrit que quand le fichier ne se déduit pas du nom
    # (fichier = nom avec des « _ ») : 221 des 269 notes le déduisent, et les
    # 7 Ko économisés sont ce qui fait tenir l'index complet dans le budget.
    def _link(name: str, fname: str) -> str:
        return name if fname == name.replace("-", "_") + ".md" else f"[{name}]({fname})"

    family_lines = [
        "- **{}** — {}".format(fam, " · ".join(_link(n, f) for n, f in sorted(notes)))
        for fam, notes in sorted(families.items())
    ]

    def render(cap: int, n_recent: int) -> str:
        recent = recent_all[:n_recent]
        title_recent = f"Touched in the last {RECENT_DAYS} days ({len(recent)}"
        title_recent += f" of {len(recent_all)}, most recent first)" if len(recent) < len(recent_all) else ")"
        lines: list[str] = []
        for title, group in (
            (f"Working rules ({len(rules)}) — to apply, not to look up", rules),
            (title_recent, recent),
        ):
            if not group:
                continue
            lines.append(f"## {title}\n")
            for name, description, source_path, _t in group:
                fname = Path(source_path).name
                desc = _truncate(" ".join(description.split()), cap)
                lines.append(f"- [{name}]({fname}) — {desc}" if desc else f"- [{name}]({fname})")
            lines.append("")
        lines.append(f"## All notes by family ({len(rows)})\n")
        lines += family_lines
        return header + "\n".join(lines) + "\n"

    def fits(candidate: str) -> bool:
        return (candidate.count("\n") <= INDEX_MAX_LINES
                and len(candidate.encode("utf-8")) <= INDEX_BUDGET)

    # Two levers: hook length (bytes), then the size of the "recent" window
    # (lines). Trim the hooks first — losing the tail of a description costs
    # less than losing a whole note from the index.
    n_kept = len(recent_all)
    content = render(200, n_kept)
    if not fits(content):
        for n_recent in range(len(recent_all), -1, -5):
            for cap in range(200, 39, -10):
                candidate = render(cap, n_recent)
                if fits(candidate):
                    n_kept, content = n_recent, candidate
                    break
            else:
                content = candidate
                continue
            break
        if not fits(content):
            print(
                f"WARN  MEMORY.md does not fit the budget ({content.count(chr(10))} lines, "
                f"{len(content.encode('utf-8'))} bytes) — archive some notes",
                file=sys.stderr,
            )

    index_path = MEMORY_DIR / "MEMORY.md"
    if index_path.exists() and index_path.read_text(encoding="utf-8") == content:
        return
    index_path.write_text(content, encoding="utf-8")
    print(
        f"MEMORY.md rebuilt: {len(rows)} entries, {len(families)} families, "
        f"{len(rules)} rules, {n_kept}/{len(recent_all)} recent, {content.count(chr(10))} lines, "
        f"{len(content.encode('utf-8'))} bytes"
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rebuild", action="store_true", help="drop and re-embed everything")
    args = ap.parse_args()

    if not MEMORY_DIR.is_dir():
        print(f"memory dir not found: {MEMORY_DIR}", file=sys.stderr)
        return 1
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)

    con = sqlite3.connect(DB_PATH)
    con.execute("PRAGMA foreign_keys = ON")
    init_db(con)
    if args.rebuild:
        con.executescript("DELETE FROM chunks; DELETE FROM notes;")
        con.commit()

    files = sorted(p for p in MEMORY_DIR.glob("*.md") if p.name != "MEMORY.md")
    seen: set[str] = set()
    known = {row[0]: row[1] for row in con.execute("SELECT name, mtime FROM notes")}
    reindexed = skipped = 0

    for path in files:
        note = parse_note(path)
        name = note["name"]
        seen.add(name)
        mtime = path.stat().st_mtime
        # Calculée pour TOUTE note, y compris celles dont l'embedding est à
        # jour : c'est elle qui alimente la fenêtre « récentes » de l'index.
        eff, eff_src = effective_modified(con, note, note["body"])
        if known.get(name) == mtime:
            con.execute(
                "UPDATE notes SET modified = ?, modified_source = ? WHERE name = ?",
                (eff, eff_src, name),
            )
            con.commit()
            skipped += 1
            continue

        chunks = chunk_body(note["body"])
        if not chunks:
            continue
        texts = [embed_text(name, note["description"], c) for c in chunks]
        vecs = embed_batch(texts)

        con.execute("DELETE FROM chunks WHERE note_name = ?", (name,))
        con.execute(
            "INSERT INTO notes(name, description, type, mtime, source_path, modified, modified_source) "
            "VALUES(?,?,?,?,?,?,?) ON CONFLICT(name) DO UPDATE SET "
            "description=excluded.description, type=excluded.type, "
            "mtime=excluded.mtime, source_path=excluded.source_path, "
            "modified=excluded.modified, modified_source=excluded.modified_source",
            (name, note["description"], note["type"], mtime, str(path), eff, eff_src),
        )
        con.executemany(
            "INSERT INTO chunks(note_name, chunk_idx, body, embedding) VALUES(?,?,?,?)",
            [(name, i, c, json.dumps(v)) for i, (c, v) in enumerate(zip(chunks, vecs))],
        )
        con.commit()
        reindexed += 1
        print(f"  · {name}: {len(chunks)} chunk(s)")

    # prune notes whose files disappeared
    pruned = [n for n in known if n not in seen]
    for n in pruned:
        con.execute("DELETE FROM chunks WHERE note_name = ?", (n,))
        con.execute("DELETE FROM notes WHERE name = ?", (n,))
    con.execute(
        "INSERT INTO meta(key,value) VALUES('model',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (embed_describe(),),
    )
    con.commit()

    write_memory_index(con)

    n_notes = con.execute("SELECT COUNT(*) FROM notes").fetchone()[0]
    n_chunks = con.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
    con.close()
    print(
        f"done: {reindexed} reindexed, {skipped} unchanged, {len(pruned)} pruned "
        f"→ {n_notes} notes / {n_chunks} chunks @ {DB_PATH}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
