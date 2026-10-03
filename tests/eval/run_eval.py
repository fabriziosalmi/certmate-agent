"""Measure retrieval quality of the docs index against retrieval_set.json.

Usage:
    python -m tests.eval.run_eval --index docs_index/index.json.gz \
        [--url http://host:1234/v1] [--cache queries.json] [--report out.json]

Answerable questions are scored on whether a chunk from an expected source
file appears in the top k (hit@k) and on the reciprocal rank of the first one
(MRR). No-answer questions have no right chunk, so they are only used to see
how their best score compares with the best score of answerable questions:
that gap is what a score floor has to separate.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
from collections import Counter, defaultdict
from pathlib import Path

from agent.llm.lmstudio import LMStudioClient
from agent.rag.store import RagStore

SET_PATH = Path(__file__).with_name("retrieval_set.json")
KS = (1, 3, 5)
DEPTH = 10


async def _embed_questions(
    questions: list[str], url: str | None, cache_path: Path | None, model: str,
) -> dict[str, list[float]]:
    cache: dict[str, list[float]] = {}
    if cache_path and cache_path.exists():
        saved = json.loads(cache_path.read_text(encoding="utf-8"))
        if saved.get("model") == model:
            cache = saved["vectors"]
    missing = [q for q in questions if q not in cache]
    if missing:
        async with LMStudioClient(base_url=url) as llm:
            vectors = await llm.embed(missing)
        cache.update(dict(zip(missing, vectors)))
        if cache_path:
            cache_path.write_text(
                json.dumps({"model": model, "vectors": cache}), encoding="utf-8",
            )
    return cache


def _rank_of_first(hits, expected: list[str]) -> int | None:
    for i, h in enumerate(hits, 1):
        if h.source in expected:
            return i
    return None


def evaluate(store: RagStore, items: list[dict], vectors: dict[str, list[float]]) -> dict:
    rows = []
    for x in items:
        hits = store.rank(vectors[x["question"]], DEPTH)
        row = {
            "id": x["id"], "question": x["question"], "kind": x["kind"],
            "lang": x["lang"], "top": [(h.source, round(h.score, 3)) for h in hits[:5]],
            "top1_score": round(hits[0].score, 3) if hits else 0.0,
        }
        if x["kind"] == "answerable":
            row["expected"] = list(x["anchors"])
            row["rank"] = _rank_of_first(hits, row["expected"])
        rows.append(row)

    ans = [r for r in rows if r["kind"] == "answerable"]
    noa = [r for r in rows if r["kind"] == "no_answer"]

    def hit_at(sub, k):
        return sum(1 for r in sub if r["rank"] is not None and r["rank"] <= k) / len(sub)

    def mrr(sub):
        return sum(1 / r["rank"] for r in sub if r["rank"]) / len(sub)

    metrics = {"n_answerable": len(ans), "n_no_answer": len(noa),
               "MRR": round(mrr(ans), 3)}
    for k in KS:
        metrics[f"hit@{k}"] = round(hit_at(ans, k), 3)

    by_lang = {}
    for lang in sorted({r["lang"] for r in ans}):
        sub = [r for r in ans if r["lang"] == lang]
        by_lang[lang] = {"n": len(sub), "hit@3": round(hit_at(sub, 3), 3),
                         "MRR": round(mrr(sub), 3)}

    by_source = defaultdict(list)
    for r in ans:
        by_source[r["expected"][0]].append(r)
    per_source = {s: {"n": len(v), "hit@3": round(hit_at(v, 3), 3),
                      "MRR": round(mrr(v), 3)} for s, v in sorted(by_source.items())}

    # Which files fill the top-5 slots, versus which files are right.
    slot_share = Counter(src for r in ans for src, _ in r["top"])
    total_slots = sum(slot_share.values())

    a_scores = sorted(r["top1_score"] for r in ans)
    n_scores = sorted(r["top1_score"] for r in noa)
    thresholds = {}
    for t in (0.15, 0.3, 0.4, 0.5, 0.55, 0.6, 0.65, 0.7):
        thresholds[str(t)] = {
            "answerable_kept": round(sum(s >= t for s in a_scores) / len(a_scores), 3),
            "no_answer_rejected": round(sum(s < t for s in n_scores) / len(n_scores), 3),
        }

    return {
        "metrics": metrics, "by_lang": by_lang, "per_source": per_source,
        "top5_slot_share": {s: round(c / total_slots, 3)
                            for s, c in slot_share.most_common()},
        "score_top1": {
            "answerable": {"min": a_scores[0], "median": round(statistics.median(a_scores), 3),
                           "max": a_scores[-1]},
            "no_answer": {"min": n_scores[0], "median": round(statistics.median(n_scores), 3),
                          "max": n_scores[-1]},
        },
        "thresholds": thresholds,
        "misses_top3": [r for r in ans if r["rank"] is None or r["rank"] > 3],
        "no_answer_rows": noa,
        "rows": rows,
    }


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--index", required=True, type=Path)
    p.add_argument("--url", default=None)
    p.add_argument("--cache", type=Path, default=None)
    p.add_argument("--report", type=Path, default=None)
    p.add_argument("--set", type=Path, default=SET_PATH, dest="set_path")
    args = p.parse_args()

    items = json.loads(args.set_path.read_text(encoding="utf-8"))["items"]
    store = RagStore(args.index)
    if not store.load():
        raise SystemExit(f"cannot load index {args.index}")
    model = store.info()["embed_model"]
    vectors = asyncio.run(_embed_questions(
        [x["question"] for x in items], args.url, args.cache, model))
    report = evaluate(store, items, vectors)
    if args.report:
        args.report.write_text(json.dumps(report, indent=1, ensure_ascii=False),
                               encoding="utf-8")
    brief = {k: v for k, v in report.items()
             if k not in ("rows", "misses_top3", "no_answer_rows")}
    print(json.dumps(brief, indent=1, ensure_ascii=False))
    print(f"\nmisses outside top 3: {len(report['misses_top3'])}")


if __name__ == "__main__":
    main()
