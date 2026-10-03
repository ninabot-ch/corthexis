"""Notifications of the review (daily digest, new critical problem, repair waiting).

Nothing is wired to a particular service: configure any number of channels.

``CORTHEXIS_NOTIFY_WEBHOOKS``  comma-separated URLs, each POSTed a message. The payload
                               follows ``CORTHEXIS_NOTIFY_FORMAT`` (one for all) or a
                               ``format+`` prefix on the URL (``slack+https://…``):
                               ``json`` (default) ``{"title", "text", "link", "kind",
                               "source": "corthexis"}`` · ``slack`` / ``mattermost``
                               ``{"text"}`` · ``discord`` ``{"content"}`` · ``ntfy``
                               plain text with a ``Title`` header · ``teams`` ``{"text"}``.
``CORTHEXIS_NOTIFY_CMD``       a shell command fed the message on stdin (``mail -s …``,
                               a chat CLI…) — credentials stay in your shell, not here.
``CORTHEXIS_PUBLIC_URL``       base URL of the dashboard, appended to messages.

Secrets in webhook URLs are never logged: errors name the host only.
"""
from __future__ import annotations

import json
import logging
import subprocess
import urllib.error
import urllib.parse
import urllib.request

from .config import env, env_list

log = logging.getLogger("corthexis.notify")
FORMATS = ("json", "slack", "mattermost", "discord", "ntfy", "teams")


def channels() -> list[tuple[str, str]]:
    """(format, url) of every webhook."""
    default = (env("NOTIFY_FORMAT") or "json").lower()
    out = []
    for raw in env_list("NOTIFY_WEBHOOKS"):
        fmt, sep, rest = raw.partition("+")
        if sep and fmt.lower() in FORMATS and rest.startswith(("http://", "https://")):
            out.append((fmt.lower(), rest))
        else:
            out.append((default if default in FORMATS else "json", raw))
    return out


def enabled() -> bool:
    return bool(channels() or env("NOTIFY_CMD"))


def public_url(path: str = "") -> str:
    base = (env("PUBLIC_URL") or "").rstrip("/")
    return base + path if base else ""


def _host(url: str) -> str:
    return urllib.parse.urlsplit(url).hostname or "?"


def payload(fmt: str, title: str, text: str, link: str, kind: str) -> tuple[bytes, dict]:
    full = f"{title}\n{text}" + (f"\n{link}" if link else "")
    if fmt in ("slack", "mattermost", "teams"):
        return json.dumps({"text": full}).encode(), {"content-type": "application/json"}
    if fmt == "discord":
        return json.dumps({"content": full[:1990]}).encode(), {"content-type": "application/json"}
    if fmt == "ntfy":
        headers = {"Title": title.encode("ascii", "replace").decode(), "Tags": "brain"}
        if link:
            headers["Click"] = link
        return (text + (f"\n{link}" if link else "")).encode(), headers
    return (json.dumps({"title": title, "text": text, "link": link or None, "kind": kind,
                        "source": "corthexis"}, ensure_ascii=False).encode(),
            {"content-type": "application/json"})


def send(title: str, text: str, link: str = "", kind: str = "memory",
         timeout: float = 10.0) -> dict[str, str]:
    """Send to every channel. → {channel label: "ok" | error}. Never raises."""
    out: dict[str, str] = {}
    for i, (fmt, url) in enumerate(channels()):
        label = f"webhook{i + 1}:{fmt}@{_host(url)}"
        data, headers = payload(fmt, title, text, link, kind)
        req = urllib.request.Request(url, data=data, method="POST",
                                     headers={"user-agent": "corthexis", **headers})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                r.read(256)
            out[label] = "ok"
        except urllib.error.HTTPError as e:
            out[label] = f"HTTP {e.code}"
        except Exception as e:  # noqa: BLE001
            out[label] = type(e).__name__
        if out[label] != "ok":
            log.warning("notification to %s failed: %s", label, out[label])
    cmd = env("NOTIFY_CMD")
    if cmd:
        msg = f"{title}\n{text}" + (f"\n{link}" if link else "")
        try:
            p = subprocess.run(cmd, shell=True, input=msg, text=True, timeout=60,
                               capture_output=True)
            out["cmd"] = "ok" if p.returncode == 0 else f"exit {p.returncode}"
        except Exception as e:  # noqa: BLE001
            out["cmd"] = type(e).__name__
        if out["cmd"] != "ok":
            log.warning("notification command failed: %s", out["cmd"])
    return out
