# Recall bench

Two benches, and what each one can prove.

## 1. The public bench — rerun it yourself

`bench/demo/questions.jsonl`: **60 questions** written for the demo corpus
(`examples/mirabeau-conseil/`, 38 notes of a fictional consulting firm, in French), asked in
**French (30), English (20) and German (10)**. Three kinds: the answer is in the note's
description (`fact`, 41), only in its body (`body`, 8), or the question shares almost no word
with the note (`paraphrase`, 11). Each line names the note(s) that answer it.

```bash
docker run -d --name cx-bench-pg -p 127.0.0.1:55432:5432 -e POSTGRES_USER=cx \
  -e POSTGRES_PASSWORD=test pgvector/pgvector:pg16
docker exec cx-bench-pg psql -U cx -d postgres -c "CREATE DATABASE bench"
# point CORTHEXIS_EMBED_URLS (and CORTHEXIS_RERANK_URL) at your model servers — the ones of
# `docker compose up` are http://127.0.0.1:8421 (embed) and :8422 (rerank) once published
CORTHEXIS_DATABASE_URL=postgresql://cx:test@127.0.0.1:55432/bench \
  python bench/demo/bench.py            # add --rerank to rerank the top results
```

Use one empty database per model. On your own memory, the same bank format works with
`corthexis eval import <file.jsonl>`, then `corthexis eval run`.

### Results, 05.10.2026

Hybrid search (dense + keywords), top 10, MRR = mean of 1/rank of the first expected note.

| Embedding | Reranker | hit@1 | hit@5 | MRR | MRR fr | MRR en | MRR de |
|---|---|---:|---:|---:|---:|---:|---:|
| MiniLM-L12 multilingual (CortHeXis 1.x) | — | 0.83 | 0.97 | 0.89 | 0.96 | 0.88 | **0.73** |
| multilingual-e5-base Q8 (MIT, fallback) | — | 0.85 | 0.98 | 0.91 | 0.97 | 0.80 | 0.95 |
| EmbeddingGemma-300m Q8 (default) | — | 0.90 | 1.00 | 0.95 | 0.96 | 0.93 | 0.95 |
| EmbeddingGemma-300m Q8 | Qwen3-Reranker-0.6B, top 10 | **0.95** | 1.00 | **0.97** | 0.98 | 0.94 | 1.00 |

Search p50 without reranker: 8 ms on this corpus; with the reranker on a GPU, about 1 s.

**Read these numbers for what they are.** 38 short, well-written notes are an easy corpus:
every model finds almost everything in the top 5, and the gaps between models are narrow.
The bench shows the ordering and two real effects — MiniLM loses German questions on French
notes, the reranker fixes most first-place misses — and it lets you check that your install
gives the same figures. It does not show how recall holds on a large, messy, real memory.

## 2. The reference bench — 300 questions on a real memory

The figures quoted in the README and on corthexis.com (MRR **0.55 → 0.82**, 0.88 with the
reranker; hit@1 42 % → 73 %) come from **300 questions** (65 % French, 25 % English, 10 %
German) over the working memory of the company that builds CortHeXis: hundreds of notes,
hundreds of thousands of words, written while running products in production. On that corpus
the gaps are wide — MiniLM reads only the first 128 tokens of a passage, so a fact that lives
in a note's body is nearly invisible to it.

That corpus and its questions are **not published** (infrastructure, clients, finances in
almost every note; a redacted copy would still leak by its shape). What is published is the
method, which is the one `corthexis eval` implements:

- a question is something someone actually asked, paired with the note(s) that answer it;
  most are harvested from session transcripts (a prompt followed by `memory_get(note)`),
  the rest written by hand, with a share whose answer is **only in the body**;
- metrics: hit@1, hit@5, MRR, nDCG@10, weighted per question; generated questions count for
  0.4 because the model that wrote them has just read the note;
- a model or profile change is judged on the **same questions** (`corthexis eval compare`),
  and a drop of more than 0.02 MRR is a regression the review reports.

A 35-question version of that bench saturated (MiniLM at 0.91 there, 0.55 on 300 questions):
a small bench flatters every model. That is why the public one above is labelled as such, and
why the bench that matters is the one you build on your own notes.
