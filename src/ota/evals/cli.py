"""Evaluation CLI.

  python -m ota.evals retrieval                       # offline: bm25 vs vector vs hybrid
  python -m ota.evals agent --model extractive        # offline stub
  python -m ota.evals agent --model scripted:eager    # offline scripted failure-mode model
  python -m ota.evals matrix                          # offline: every model x {guarded, trusting}
  python -m ota.evals agent --model claude --confirm-live   # LIVE: real API, costs money
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..app import ConfigError
from ..config import Settings
from .corpus import build_store
from .dataset import load_dataset
from .retrieval import run_retrieval_suite
from .runner import MODEL_CHOICES, run_one

OFFLINE_MODELS = [m for m in MODEL_CHOICES if m != "claude"]
ROWS = ["accuracy", "answer_recall", "abstain_accuracy", "false_answer_rate",
        "unsafe_output_rate", "invalid_citation_rate", "ungrounded_rate"]


def _fmt(v: dict[str, Any]) -> str:
    return f"{v['n']}/{v['of']}" if v["of"] else "n/a"


def matrix_markdown(runs: list[dict[str, Any]]) -> str:
    head = "| model | agent | " + " | ".join(ROWS) + " |\n|" + "---|" * (len(ROWS) + 2)
    lines = [head]
    for r in runs:
        s = r["summary"]
        lines.append(f"| {r['meta']['model']} | {r['meta']['agent_mode']} | "
                     + " | ".join(_fmt(s[k]) for k in ROWS) + " |")
    return "\n".join(lines)


def retrieval_markdown(res: dict[str, Any]) -> str:
    out = []
    for name, e in res.items():
        out.append(f"\n**{name}** ({e['documents']} documents)\n")
        out.append("| mode | hit@1 | hit@3 | hit@5 | MRR | direct hit@1 | paraphrase hit@1 | missed@5 |")
        out.append("|---|---|---|---|---|---|---|---|")
        for mode, m in e["modes"].items():
            a, d, p = m["all"], m["direct"], m["paraphrase"]
            out.append(f"| {mode} | {a['hit@1']}/{a['n']} | {a['hit@3']}/{a['n']} | {a['hit@5']}/{a['n']} | "
                       f"{a['mrr']} | {d['hit@1']}/{d['n']} | {p['hit@1']}/{p['n']} | {', '.join(m['missed_at_5']) or '-'} |")
        sw, nsw = e["spurious_with_stopwords"], e["spurious_without_stopwords"]
        out.append("\nSpurious retrieval on unanswerable/out-of-scope questions "
                   "(lower is better; these questions have no right document):\n")
        out.append("| mode | with stopword filter | without |\n|---|---|---|")
        for mode in ("bm25", "hybrid"):
            out.append(f"| {mode} | {sw[mode]['n']}/{sw[mode]['of']} | {nsw[mode]['n']}/{nsw[mode]['of']} |")
        out.append("\nHybrid vs vector similarity floor (0.05 = original setting, 0.1325 = 3/sqrt(512) = current default):\n")
        out.append("| floor | hit@1 | hit@3 | hit@5 | MRR | spurious on unanswerable |\n|---|---|---|---|---|---|")
        for floor, s in e["vector_floor_sweep"].items():
            out.append(f"| {floor} | {s['hit@1']}/{s['n']} | {s['hit@3']}/{s['n']} | {s['hit@5']}/{s['n']} | "
                       f"{s['mrr']} | {s['spurious']}/{s['spurious_of']} |")
    return "\n".join(out)


def _write(path: str | Path, obj: Any) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj, indent=2, ensure_ascii=False) + "\n")
    print(f"wrote {p}", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m ota.evals")
    p.add_argument("--dataset", default="evals/dataset.jsonl")
    p.add_argument("--fixtures-dir", default="fixtures")
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("retrieval", help="offline: bm25 vs vector vs hybrid, stopword ablation")
    r.add_argument("--distractors", type=int, nargs="+", default=[0, 300])
    r.add_argument("--out", default="evals/results/retrieval.json")

    a = sub.add_parser("agent", help="run the agent over the dataset")
    a.add_argument("--model", choices=MODEL_CHOICES, default="extractive")
    a.add_argument("--agent", choices=["guarded", "trusting"], default="guarded")
    a.add_argument("--distractors", type=int, default=0)
    a.add_argument("--retrieval-mode", choices=["bm25", "hybrid", "vector"], default=None,
                   help="default: OTA_RETRIEVAL_MODE or bm25")
    a.add_argument("--delay", type=float, default=0.0, help="seconds between questions (live runs)")
    a.add_argument("--confirm-live", action="store_true", help="required for --model claude")
    a.add_argument("--out", default="")

    m = sub.add_parser("matrix", help="offline: all models x guarded/trusting")
    m.add_argument("--out", default="evals/results/offline_matrix.json")
    args = p.parse_args(argv)

    questions = load_dataset(args.dataset)
    settings = Settings.from_env()

    if args.cmd == "retrieval":
        res = run_retrieval_suite(args.fixtures_dir, questions, args.distractors)
        _write(args.out, {"meta": {"kind": "offline-retrieval", "embedder": "hashing-v1-512 (lexical)",
                                   "questions": len(questions)}, "results": res})
        print(retrieval_markdown(res))
        return 0

    if args.cmd == "agent":
        live = args.model == "claude"
        if live and not args.confirm_live:
            print("REFUSING: --model claude makes LIVE API calls (cost, and your question text plus "
                  "retrieved content go to the provider). Re-run with --confirm-live.", file=sys.stderr)
            return 2
        if live and not settings.anthropic_api_key:
            print("CONFIG ERROR: ANTHROPIC_API_KEY is not set.", file=sys.stderr)
            return 2
        store = build_store(args.fixtures_dir, args.distractors)
        corpus = {"kind": "synthetic-fixtures", "documents": store.count_records(),
                  "distractors": args.distractors}
        try:
            run = run_one(store, questions, args.dataset, choice=args.model, mode=args.agent,
                          settings=settings, corpus=corpus, delay_s=args.delay,
                          retrieval_mode=args.retrieval_mode)
        except ConfigError as exc:
            print(f"CONFIG ERROR: {exc}", file=sys.stderr)
            return 2
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        safe = run["meta"]["model_choice"].replace(":", "-")
        out = args.out or (f"evals/results/live_{settings.model}_{args.agent}_{stamp}.json" if live
                           else f"evals/results/offline_{safe}_{args.agent}.json")
        if live:
            run["meta"]["model"] = settings.model
        _write(out, run)
        print(f"[{run['meta']['kind']}] model={run['meta']['model']} agent={args.agent}")
        print(matrix_markdown([run]))
        if live:
            print(f"estimated cost USD: {run['summary']['est_cost_usd']} "
                  "(null unless OTA_PRICE_IN_PER_MTOK / OTA_PRICE_OUT_PER_MTOK are set)")
        return 0

    # matrix (always offline)
    runs = []
    for choice in OFFLINE_MODELS:
        for mode in ("guarded", "trusting"):
            store = build_store(args.fixtures_dir, 0)
            corpus = {"kind": "synthetic-fixtures", "documents": store.count_records(), "distractors": 0}
            runs.append(run_one(store, questions, args.dataset, choice=choice, mode=mode,
                                settings=settings, corpus=corpus))
    _write(args.out, {"meta": {"kind": "offline-matrix", "note": "scripted models are failure-mode "
                                "simulations, NOT Claude", "questions": len(questions)}, "runs": runs})
    print(matrix_markdown(runs))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
