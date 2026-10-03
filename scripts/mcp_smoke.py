#!/usr/bin/env python3
"""MCP smoke test of a running CortHeXis: list the tools, search, read the first hit.

    python3 scripts/mcp_smoke.py URL TOKEN "question" [expected-note]

Exit 0 when the tools are there, the search answers, and the expected note (if given) is in
the top 3. Needs the MCP client library (``pip install "mcp>=1.12,<2"``).
"""
import asyncio
import json
import sys

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client


async def main(url: str, token: str, query: str, expected: str | None) -> int:
    async with streamablehttp_client(url, headers={"Authorization": f"Bearer {token}"}) as (r, w, _):
        async with ClientSession(r, w) as s:
            await s.initialize()
            tools = {t.name for t in (await s.list_tools()).tools}
            print("tools:", ", ".join(sorted(tools)))
            if not {"memory_search", "memory_get"} <= tools:
                return 1
            res = await s.call_tool("memory_search", {"query": query, "top_k": 3})
            hits = [json.loads(c.text) for c in res.content]
            for h in hits:
                print(f"  {h.get('score')}  {h.get('note_name')}  {h.get('degraded') or ''}")
            if not hits or "note_name" not in hits[0]:
                return 1
            note = (await s.call_tool("memory_get", {"note_name": hits[0]["note_name"]}))
            print(note.content[0].text.splitlines()[0])
            if expected and expected not in [h["note_name"] for h in hits]:
                print(f"expected {expected} in the top 3")
                return 1
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 4:
        print(__doc__)
        sys.exit(2)
    sys.exit(asyncio.run(main(sys.argv[1], sys.argv[2], sys.argv[3],
                              sys.argv[4] if len(sys.argv) > 4 else None)))
