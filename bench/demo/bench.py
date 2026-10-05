"""Public recall bench: the demo corpus (examples/mirabeau-conseil) and 60 written questions
in French, English and German (bench/demo/questions.jsonl). Anyone can rerun it and compare.

    CORTHEXIS_DATABASE_URL=postgresql://… python bench/demo/bench.py [--rerank]

Indexes the corpus into the given database with the configured embedder (CORTHEXIS_* as for
the service), imports the questions, runs the bench and prints hit@1, hit@5 and MRR overall,
by language and by kind of question. Use an empty database: the corpus becomes its active
index generation.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from corthexis import embed  # noqa: E402
from corthexis import eval as ev  # noqa: E402
from corthexis.indexer import IndexConfig, Indexer  # noqa: E402
from corthexis.store import Store  # noqa: E402


def table(rows: dict[str, list[dict]]) -> str:
    out = ["| | questions | hit@1 | hit@5 | MRR |", "|---|---:|---:|---:|---:|"]
    for label, rs in rows.items():
        s = ev.summarize(rs)
        if s["n"]:
            out.append(f"| {label} | {s['n']} | {s['h1']:.2f} | {s['h5']:.2f} | {s['mrr']:.2f} |")
    return "\n".join(out)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--dsn", default=None)
    ap.add_argument("--notes", default=str(ROOT / "examples" / "mirabeau-conseil"))
    ap.add_argument("--questions", default=str(Path(__file__).with_name("questions.jsonl")))
    ap.add_argument("--rerank", action="store_true", help="rerank the top results")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    store = Store(a.dsn)
    e = embed.get()
    try:
        rep = Indexer(store, e, IndexConfig(memory_dir=Path(a.notes), normalize=False,
                                            write_index=False), log=lambda m: None).run()
        if not rep.activated and store.active_generation() is None:
            print("indexing did not produce an active generation", file=sys.stderr)
            return 1
        qs = ev.import_questions(store, a.questions)
        run = ev.run(store, e, profile=embed.current_profile(), trigger="bench",
                     rerank=a.rerank, question_ids=[q.id for q in qs])
        ranks = ev.question_ranks(store, run["id"])
        rows: dict[str, list[dict]] = {"all": []}
        for q in qs:
            if q.id not in ranks:
                continue
            r = ranks[q.id]
            row = {"h1": int(r == 1), "h5": int(r is not None and r <= 5),
                   "mrr": 1 / r if r else 0.0, "ndcg10": 0.0, "weight": 1.0}
            rows["all"].append(row)
            for key in ("lang", "kind"):
                rows.setdefault(f"{key}: {q.origin.get(key, '?')}", []).append(row)
        if a.json:
            print(json.dumps({"identity": run["embed_identity"], "rerank": a.rerank,
                              "summary": {k: ev.summarize(v) for k, v in rows.items()},
                              "search_ms_p50": run["metrics"]["search_ms_p50"]}, indent=2))
        else:
            print(f"{run['embed_identity']} — reranker {'on' if a.rerank else 'off'} — "
                  f"search p50 {run['metrics']['search_ms_p50']} ms\n")
            print(table(rows))
    finally:
        store.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
