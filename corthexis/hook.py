#!/usr/bin/env python3
"""Recall hook for Claude Code — the memory comes to the agent, at every message.

``UserPromptSubmit`` → the notes related to the message are added to the context;
``PreToolUse`` on ``Task|Agent`` → the recall is appended to the sub-agent's prompt (a
sub-agent otherwise starts with no memory at all). Notes already given in the session
are not given again.

Standard library only: this file runs as a plain script from a clone, with no package
installed (``python3 corthexis/hook.py …``), or as ``corthexis hook …``.

    python3 corthexis/hook.py install [--scope user|project|local] [--url URL] [--token-file F]
    python3 corthexis/hook.py uninstall [--scope …]
    python3 corthexis/hook.py show                   # the settings it would write
    python3 corthexis/hook.py run                    # what Claude Code calls (stdin = event)

``run`` posts the event to the running service (``POST {url}/api/recall/hook``, Bearer
token) — warm, ~50-150 ms. If the service does not answer and the package is importable
with ``CORTHEXIS_DATABASE_URL`` set, the recall runs in this process instead (~1 s). It
never fails a turn: any error = no output, exit 0. Debug: ``CORTHEXIS_RECALL_DEBUG=1``::

    echo '{"hook_event_name":"UserPromptSubmit","session_id":"t","prompt":"how do we deploy?"}' \\
      | CORTHEXIS_RECALL_DEBUG=1 python3 corthexis/hook.py run

Configuration: ``CORTHEXIS_URL`` and ``CORTHEXIS_TOKEN`` / ``CORTHEXIS_TOKEN_FILE``, else
the file written by ``install``: ``~/.config/corthexis/hook.json``.
"""
from __future__ import annotations

import argparse
import io
import json
import os
import shlex
import stat
import sys
import urllib.error
import urllib.request
from pathlib import Path

MARK = "corthexis"          # our hook commands contain it: install/uninstall find them by it
EVENTS = (("UserPromptSubmit", None), ("PreToolUse", "Task|Agent"))
DEFAULT_URL = "http://localhost:8420"


def _debug(msg: str) -> None:
    if os.environ.get("CORTHEXIS_RECALL_DEBUG") == "1":
        print(f"[corthexis-recall] {msg}", file=sys.stderr)


def config_dir() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return Path(base) / "corthexis"


def load_config() -> dict:
    cfg: dict = {}
    try:
        cfg = json.loads((config_dir() / "hook.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass
    if os.environ.get("CORTHEXIS_URL"):
        cfg["url"] = os.environ["CORTHEXIS_URL"]
    if os.environ.get("CORTHEXIS_TOKEN_FILE"):
        cfg["token_file"] = os.environ["CORTHEXIS_TOKEN_FILE"]
    token = os.environ.get("CORTHEXIS_TOKEN", "")
    if not token and cfg.get("token_file"):
        try:
            token = Path(cfg["token_file"]).expanduser().read_text(encoding="utf-8").strip()
        except OSError:
            token = ""
    cfg["token"] = token
    cfg.setdefault("url", DEFAULT_URL)
    return cfg


# --------------------------------------------------------------------------- run
def via_api(raw: bytes, cfg: dict) -> bool:
    """True when the service answered (its answer, possibly empty, is printed)."""
    if not cfg.get("token"):
        _debug("no token configured")
        return False
    try:
        req = urllib.request.Request(
            cfg["url"].rstrip("/") + "/api/recall/hook", data=raw, method="POST",
            headers={"content-type": "application/json",
                     "authorization": f"Bearer {cfg['token']}"})
        with urllib.request.urlopen(req, timeout=float(
                os.environ.get("CORTHEXIS_RECALL_API_TIMEOUT", "4"))) as r:
            out = r.read()
    except urllib.error.HTTPError as e:
        _debug(f"service answered {e.code}")
        return e.code not in (401, 403, 404, 502, 503)
    except (OSError, ValueError) as e:
        _debug(f"service unreachable: {e!r}")
        return isinstance(e, TimeoutError)          # a timeout: no second, slower try
    if out.strip() not in (b"", b"{}"):
        sys.stdout.write(out.decode("utf-8"))
    return True


def run() -> int:
    raw = sys.stdin.buffer.read()
    try:
        json.loads(raw or b"{}")
    except ValueError:
        return 0
    if via_api(raw, load_config()):
        return 0
    if not os.environ.get("CORTHEXIS_DATABASE_URL"):
        return 0
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
        from corthexis.recall import main as recall_main
    except Exception as e:  # noqa: BLE001 — package not installed: no recall, never a broken turn
        _debug(f"in-process recall unavailable: {e!r}")
        return 0
    return recall_main(io.StringIO(raw.decode("utf-8", "replace")))


# --------------------------------------------------------------------------- install
def settings_path(scope: str, project_dir: str | None = None) -> Path:
    if scope == "user":
        base = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.expanduser("~/.claude")
        return Path(base) / "settings.json"
    root = Path(project_dir or os.getcwd())
    return root / ".claude" / ("settings.json" if scope == "project" else "settings.local.json")


def hook_command(python: str | None = None) -> str:
    me = Path(__file__).resolve()
    return f"{shlex.quote(python or sys.executable)} {shlex.quote(str(me))} run"


def _strip(hooks: dict) -> dict:
    """The hooks map without our entries (other hooks are kept untouched)."""
    out = {}
    for event, groups in (hooks or {}).items():
        kept = []
        for g in groups or []:
            hs = [h for h in (g or {}).get("hooks") or [] if MARK not in str(h.get("command", ""))]
            if hs:
                kept.append({**g, "hooks": hs})
            elif not (g or {}).get("hooks"):
                kept.append(g)
        if kept:
            out[event] = kept
    return out


def with_hooks(settings: dict, command: str, timeout: int = 10) -> dict:
    hooks = _strip(settings.get("hooks") or {})
    for event, matcher in EVENTS:
        group: dict = {"hooks": [{"type": "command", "command": command, "timeout": timeout}]}
        if matcher:
            group = {"matcher": matcher, **group}
        hooks.setdefault(event, []).append(group)
    return {**settings, "hooks": hooks}


def _read(path: Path) -> dict:
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
        return doc if isinstance(doc, dict) else {}
    except FileNotFoundError:
        return {}
    except ValueError as e:
        raise SystemExit(f"{path} is not valid JSON ({e}); fix it first, nothing was changed")


def _write(path: Path, doc: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def install(scope: str, url: str, token: str | None, token_file: str | None,
            project_dir: str | None = None, python: str | None = None) -> Path:
    cdir = config_dir()
    cdir.mkdir(parents=True, exist_ok=True)
    if token:
        tf = cdir / "token"
        tf.write_text(token.strip() + "\n", encoding="utf-8")
        os.chmod(tf, stat.S_IRUSR | stat.S_IWUSR)
        token_file = str(tf)
    cfg = {"url": url.rstrip("/")}
    if token_file:
        cfg["token_file"] = str(Path(token_file).expanduser().resolve())
    _write(cdir / "hook.json", cfg)
    path = settings_path(scope, project_dir)
    _write(path, with_hooks(_read(path), hook_command(python)))
    return path


def uninstall(scope: str, project_dir: str | None = None) -> Path:
    path = settings_path(scope, project_dir)
    doc = _read(path)
    if doc.get("hooks") is not None:
        hooks = _strip(doc["hooks"])
        doc = {**doc, "hooks": hooks} if hooks else {k: v for k, v in doc.items() if k != "hooks"}
        _write(path, doc)
    return path


def _token_from_env_file(path: str) -> str | None:
    try:
        for line in Path(path).read_text(encoding="utf-8").splitlines():
            k, _, v = line.partition("=")
            if k.strip() == "CORTHEXIS_TOKEN" and v.strip():
                return v.strip().strip("'\"")
    except OSError:
        pass
    return None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="corthexis hook", description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("run", help="the hook itself (Claude Code calls it, event on stdin)")
    for name in ("install", "uninstall", "show"):
        s = sub.add_parser(name)
        s.add_argument("--scope", choices=("user", "project", "local"), default="user",
                       help="user: ~/.claude/settings.json (every session) · project: "
                            ".claude/settings.json · local: .claude/settings.local.json")
        s.add_argument("--project-dir", help="project root for --scope project|local")
        if name != "uninstall":
            s.add_argument("--url", default=os.environ.get("CORTHEXIS_URL", DEFAULT_URL),
                           help=f"the CortHeXis service (default {DEFAULT_URL})")
            s.add_argument("--token", help="the service token (CORTHEXIS_TOKEN)")
            s.add_argument("--token-file", help="a file holding the token")
            s.add_argument("--env-file", default=".env",
                           help="read CORTHEXIS_TOKEN from this file (default ./.env)")
            s.add_argument("--python", help="interpreter for the hook (default: this one)")
    a = ap.parse_args(argv)
    if a.cmd in (None, "run"):
        try:
            return run()
        except Exception as e:  # noqa: BLE001
            _debug(f"error: {e!r}")
            return 0
    if a.cmd == "uninstall":
        print(f"recall hook removed from {uninstall(a.scope, a.project_dir)}")
        return 0
    token = a.token or os.environ.get("CORTHEXIS_TOKEN") or (
        None if a.token_file else _token_from_env_file(a.env_file))
    if a.cmd == "show":
        print(json.dumps(with_hooks({}, hook_command(a.python)), indent=2))
        return 0
    if not (token or a.token_file):
        print("no token: pass --token, --token-file, or run from the folder whose .env holds "
              "CORTHEXIS_TOKEN", file=sys.stderr)
        return 2
    path = install(a.scope, a.url, token, a.token_file, a.project_dir, a.python)
    print(f"recall hook installed in {path} (service {a.url}); new sessions get it — "
          f"check with: echo '{{\"hook_event_name\":\"UserPromptSubmit\",\"session_id\":\"t\","
          f"\"prompt\":\"what do we know about deployments?\"}}' | {hook_command(a.python)}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception as e:  # noqa: BLE001
        _debug(f"error: {e!r}")
        sys.exit(0)
