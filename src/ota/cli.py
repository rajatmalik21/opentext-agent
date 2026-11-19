"""CLI: ingest | search | ask | serve | stats.   python -m ota.cli --help"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .app import ConfigError, build_agent
from .config import Settings
from .ingest.client import ClientConfig, FixtureClient, SearchClient, SourceAccessError
from .ingest.pipeline import ingest
from .obs import configure_logging
from .retrieval.embedder import HashingEmbedder
from .retrieval.hybrid import HybridIndex
from .server import make_server
from .store.sqlite_store import SQLiteStore


def _print_answer(d: dict[str, object], as_json: bool) -> None:
    if as_json:
        print(json.dumps(d, indent=2, ensure_ascii=False))
        return
    print(f"[{d['status']}] {d['answer']}")
    for c in d["citations"]:  # type: ignore[attr-defined]
        print(f"  - {c['id']}: {c['title']}\n    {c['url']}")
    for w in d["warnings"]:  # type: ignore[attr-defined]
        print(f"  ! {w}")


def main(argv: list[str] | None = None) -> int:
    settings = Settings.from_env()
    p = argparse.ArgumentParser(prog="ota")
    p.add_argument("--db", default=settings.db_path)
    sub = p.add_subparsers(dest="cmd", required=True)

    ing = sub.add_parser("ingest")
    ing.add_argument("--source", choices=["fixtures", "live"], default="fixtures")
    ing.add_argument("--fixtures-dir", default="fixtures")
    ing.add_argument("--page-size", type=int, default=5)
    ing.add_argument("--max-pages", type=int, default=50)
    ing.add_argument("--cache-dir", default="data/cache")
    ing.add_argument("--no-sweep", action="store_true", help="do not remove records absent upstream")

    se = sub.add_parser("search")
    se.add_argument("query")
    se.add_argument("-k", type=int, default=5)
    se.add_argument("--mode", choices=["hybrid", "bm25", "vector"], default="hybrid")

    ask = sub.add_parser("ask")
    ask.add_argument("question")
    ask.add_argument("--model", choices=["extractive", "claude"], default="extractive")
    ask.add_argument("--json", action="store_true")

    srv = sub.add_parser("serve")
    srv.add_argument("--model", choices=["extractive", "claude"], default="extractive")
    srv.add_argument("--host", default="127.0.0.1")
    srv.add_argument("--port", type=int, default=8080)

    sub.add_parser("stats")
    args = p.parse_args(argv)

    configure_logging(settings.log_level)
    store = SQLiteStore(args.db)
    embedder = HashingEmbedder()

    if args.cmd == "ingest":
        if args.source == "fixtures":
            client: FixtureClient | SearchClient = FixtureClient(Path(args.fixtures_dir))
        else:
            client = SearchClient(ClientConfig(cache_dir=Path(args.cache_dir)))
        try:
            report = ingest(client, store, embedder, page_size=args.page_size,
                            max_pages=args.max_pages, sweep=not args.no_sweep)
        except SourceAccessError as exc:
            print(f"ACCESS ERROR: {exc}", file=sys.stderr)
            return 2
        print(json.dumps(report.as_dict(), indent=2))
    elif args.cmd == "search":
        for h in HybridIndex(store, embedder).search(args.query, k=args.k, mode=args.mode):
            print(f"{h.score:.4f}  [{h.product}] {h.title}\n        {h.url}")
    elif args.cmd in ("ask", "serve"):
        try:
            agent = build_agent(settings, store, args.model)
        except ConfigError as exc:
            print(f"CONFIG ERROR: {exc}", file=sys.stderr)
            return 2
        if args.cmd == "ask":
            r = agent.ask(args.question).to_dict()
            _print_answer(r, args.json)
            return 0 if r["status"] in ("answered", "not_found") else 1
        server = make_server(agent, store, host=args.host, port=args.port, api_key=settings.api_key)
        print(f"serving on http://{args.host}:{args.port}  (model={args.model})", file=sys.stderr)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            server.shutdown()
    else:
        print(json.dumps({"records": store.count_records(), "chunks": store.count_chunks(),
                          "products": store.list_products()}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
