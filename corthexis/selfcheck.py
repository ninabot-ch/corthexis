#!/usr/bin/env python3
"""Is the memory actually there, and healthy?

This exists because memory fails *quietly*. Twice, in production, it went down
without anyone noticing:

  - the MCP server got overwritten in the config while another server was being
    added — nine days of sessions ran with no recall at all;
  - the generated index outgrew the harness line budget, and dozens of notes
    became invisible at session start.

Neither failure was loud. In both cases the session boots normally and simply
knows less. An agent that has forgotten does not announce it — it just gets
worse, and you attribute that to the model.

So: verify, never assume.

  1. the MCP server is declared in **user** scope — otherwise a session started
     outside the project directory boots with no memory;
  2. the MCP server is declared in **project** scope, for clones on other hosts;
  3. the server actually **starts and answers** (MCP handshake + tools/list);
  4. the generated index fits the harness budget (lines AND bytes);
  5. the index is **exhaustive**: as many entries as notes on disk;
  6. the database is in sync, and no note has lost its description — the
     signature of a broken frontmatter block.

Exit 0 = healthy (and silent). Otherwise: a report on stdout and exit 1.

    python -m corthexis.selfcheck
    python -m corthexis.selfcheck --alert    # also run $CORTHEXIS_ALERT_CMD

``CORTHEXIS_ALERT_CMD`` is any shell command; the report is passed on stdin. That
keeps credentials for your chat/paging system out of this repo:

    export CORTHEXIS_ALERT_CMD='curl -sf -X POST "$SLACK_WEBHOOK" --data-binary @-'
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import sqlite3
import subprocess
import sys

NOTES_DIR = pathlib.Path(os.environ.get("CORTHEXIS_NOTES_DIR", "./notes")).expanduser()
DB_PATH = pathlib.Path(
    os.environ.get("CORTHEXIS_DB", "~/.local/share/corthexis/memory.db")
).expanduser()
USER_CONF = pathlib.Path(
    os.environ.get("CORTHEXIS_USER_MCP_CONFIG", "~/.claude.json")
).expanduser()
PROJECT_CONF = pathlib.Path(
    os.environ.get("CORTHEXIS_PROJECT_MCP_CONFIG", "./.mcp.json")
).expanduser()
SERVER = os.environ.get("CORTHEXIS_MCP_SERVER_NAME", "corthexis")
ALERT_CMD = os.environ.get("CORTHEXIS_ALERT_CMD", "")

# The harness loads the whole index into every session and truncates past these.
MAX_LINES = int(os.environ.get("CORTHEXIS_INDEX_MAX_LINES", "190"))
MAX_BYTES = int(os.environ.get("CORTHEXIS_INDEX_MAX_BYTES", "24000"))
# Pure-reference notes legitimately carry no date. Past this, it is a regression.
UNDATED_TOLERATED = int(os.environ.get("CORTHEXIS_UNDATED_TOLERATED", "10"))


def _note_name(path: pathlib.Path) -> str:
    """Recall name of a note: frontmatter `name:`, else the filename stem."""
    head = path.read_text(encoding="utf-8", errors="replace")[:4000]
    m = re.search(r"^name:\s*(?:>-\s*\n\s*)?(.+)$", head, re.M)
    return (m.group(1).strip().strip("\"'") if m else path.stem) or path.stem


def _servers(path: pathlib.Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8")).get("mcpServers") or {}
    except (OSError, ValueError):
        return {}


def _handshake(timeout: int = 60) -> str:
    """Start the server over stdio and ask for its tools. '' means healthy."""
    proc = subprocess.Popen(
        [sys.executable, "-m", "corthexis.server"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        text=True, bufsize=1,
    )
    send = lambda obj: (proc.stdin.write(json.dumps(obj) + "\n"), proc.stdin.flush())
    try:
        send({"jsonrpc": "2.0", "id": 1, "method": "initialize",
              "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                         "clientInfo": {"name": "corthexis-selfcheck", "version": "1"}}})
        send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        send({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})

        tools: set[str] = set()
        for line in proc.stdout:
            try:
                payload = json.loads(line)
            except ValueError:
                continue
            if payload.get("id") == 2:
                for tool in (payload.get("result") or {}).get("tools") or []:
                    tools.add(tool.get("name"))
                break
        else:
            return f"the server did not answer (timeout {timeout}s)"
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()

    missing = {"memory_search", "memory_get"} - tools
    if missing:
        return f"missing tools at handshake: {', '.join(sorted(missing))}"
    return ""


def check() -> list[str]:
    problems: list[str] = []

    if SERVER not in _servers(USER_CONF):
        problems.append(
            f"`{SERVER}` missing from user scope ({USER_CONF}) -> any session started "
            f"outside the project directory boots WITHOUT memory. "
            f"Fix: `claude mcp add -s user {SERVER} ...`"
        )
    if PROJECT_CONF.exists() and SERVER not in _servers(PROJECT_CONF):
        problems.append(f"`{SERVER}` missing from {PROJECT_CONF} (project scope)")

    err = _handshake()
    if err:
        problems.append(f"the MCP server does not serve its tools: {err}")

    notes = [p for p in NOTES_DIR.glob("*.md") if p.name != "MEMORY.md"]
    if not notes:
        problems.append(f"no notes found in {NOTES_DIR} (set CORTHEXIS_NOTES_DIR)")

    index = NOTES_DIR / "MEMORY.md"
    if not index.exists():
        problems.append(f"{index} missing — run `python -m corthexis.index`")
    else:
        raw = index.read_text(encoding="utf-8")
        n_lines, n_bytes = raw.count("\n"), len(raw.encode("utf-8"))
        if n_lines > MAX_LINES or n_bytes > MAX_BYTES:
            problems.append(
                f"MEMORY.md over budget ({n_lines} lines / {n_bytes} bytes; "
                f"max {MAX_LINES} / {MAX_BYTES}) -> the harness truncates its tail at startup"
            )
        absent = [p.name for p in notes if _note_name(p) not in raw]
        if absent:
            problems.append(
                f"{len(absent)} note(s) absent from the index loaded at startup: "
                + ", ".join(sorted(absent)[:5]) + ("..." if len(absent) > 5 else "")
            )

    try:
        con = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
        indexed = con.execute("SELECT count(*) FROM notes").fetchone()[0]
        blank = [r[0] for r in con.execute(
            "SELECT name FROM notes WHERE description IS NULL OR trim(description) = ''")]
        undated = [r[0] for r in con.execute(
            "SELECT name FROM notes WHERE modified IS NULL OR trim(modified) = ''")]
        # One single mtime across the whole corpus means a bulk rewrite ran.
        # Harmless once dates live in the frontmatter, but worth knowing the day it happens.
        same_mtime = len({round(p.stat().st_mtime) for p in notes}) <= 2 and len(notes) > 20
    except sqlite3.Error as exc:
        problems.append(f"database unreadable ({DB_PATH}): {exc}")
    else:
        if indexed != len(notes):
            problems.append(
                f"database out of sync: {indexed} notes indexed for {len(notes)} files"
            )
        if blank:
            problems.append(
                f"{len(blank)} note(s) with no description = broken frontmatter, degraded recall: "
                + ", ".join(sorted(blank)[:5]) + ("..." if len(blank) > 5 else "")
            )
        if len(undated) > UNDATED_TOLERATED:
            problems.append(
                f"{len(undated)} note(s) with no last-updated date (tolerated: {UNDATED_TOLERATED}) "
                "-> they come back from search with no age, and a note from months ago "
                "reads like yesterday's: "
                + ", ".join(sorted(undated)[:5]) + ("..." if len(undated) > 5 else "")
            )
        if same_mtime:
            problems.append(
                f"all {len(notes)} notes share one mtime -> a bulk rewrite touched the corpus. "
                "Check that `metadata.modified` was not overwritten."
            )
    return problems


def alert(text: str) -> None:
    """Pipe the report into $CORTHEXIS_ALERT_CMD, if one is configured."""
    if not ALERT_CMD:
        print("--alert given but CORTHEXIS_ALERT_CMD is not set", file=sys.stderr)
        return
    subprocess.run(ALERT_CMD, shell=True, input=text, text=True, check=False)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--alert", action="store_true",
                    help="pipe the report into $CORTHEXIS_ALERT_CMD on failure")
    args = ap.parse_args()

    problems = check()
    if not problems:
        print("memory OK — MCP server reachable, index complete and within budget")
        return 0

    report = "\n".join(f"- {p}" for p in problems)
    print("MEMORY DEGRADED:\n" + report, file=sys.stderr)
    if args.alert:
        alert("Memory degraded\n\n" + report)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
