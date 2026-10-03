# A public, read-only showcase

`docker/compose.demo.yml` turns an install into a demonstration anyone can open — this is
what serves [demo.corthexis.com](https://demo.corthexis.com) on the fictional corpus of
`examples/mirabeau-conseil/`.

```bash
./setup.sh examples/mirabeau-conseil
docker compose -f docker-compose.yml -f docker/compose.demo.yml up -d
```

What changes with `CORTHEXIS_DEMO=1`:

- the dashboard is readable without the token, with a banner saying the corpus is
  fictional (`CORTHEXIS_DEMO_BANNER` replaces the text, plain text only);
- every write answers 403: no review run on demand, no repair proposal or approval, no
  bench question; the token cannot unlock them;
- `/mcp` answers 404 (`CORTHEXIS_DEMO_MCP=1` opens it, still behind the token);
- the notes are mounted read-only, `CORTHEXIS_NORMALIZE=false` and
  `CORTHEXIS_WRITE_INDEX=false`: the indexer and the reviewer only read them;
- the page is indexable by search engines (`robots: index`).

The indexer and the hourly review keep running, so the demo shows a live score and the
graph follows the files. To show the bench, add questions before switching to demo mode
(`corthexis eval add "question" expected-note`) and run it once (`corthexis eval run`).

**Do not put a real memory behind a public demo.** The review and the graph show the
notes' names, descriptions and bodies to anyone; a demo corpus must be written for the
purpose. Put a TLS proxy in front (the compose binds loopback), and send an old host name
to the public one with `CORTHEXIS_REDIRECT_HOSTS` (comma-separated) +
`CORTHEXIS_PUBLIC_URL` — a 301 that keeps the path; `/healthz` is never redirected, so a
monitor can probe the service under any name.

`/healthz` answers `ok`, or 503 with the reasons: notes unreadable, index out of sync with
the files, database unreachable, review stopped. It measures the service, not the content:
the findings of the review do not make it fail.
