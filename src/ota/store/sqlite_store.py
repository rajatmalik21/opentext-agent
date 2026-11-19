"""SQLite store: records + chunks + FTS5 (BM25) + float32 embedding blobs.

Single file, zero services, transactional, good enough for a PoC corpus. Vector search is
a brute-force matrix product (see ADR-0003 for the scaling limit and the swap path).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import numpy as np

from ..models import SourceRecord
from ..retrieval.embedder import Embedder

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS records (
  url TEXT PRIMARY KEY,
  source_id TEXT NOT NULL,
  product TEXT, title TEXT NOT NULL, summary TEXT NOT NULL,
  resource_type TEXT, last_updated TEXT,
  source TEXT NOT NULL, source_url TEXT NOT NULL,
  fetched_at TEXT NOT NULL, content_hash TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS chunks (
  chunk_id INTEGER PRIMARY KEY AUTOINCREMENT,
  url TEXT NOT NULL REFERENCES records(url) ON DELETE CASCADE,
  ord INTEGER NOT NULL, text TEXT NOT NULL, embedding BLOB NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_chunks_url ON chunks(url);
CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
  text, title, product, tokenize = 'porter unicode61'
);
"""


class EmbedderMismatchError(RuntimeError):
    """Index was built with a different embedder; rebuild required."""


class SQLiteStore:
    def __init__(self, path: str | Path = ":memory:") -> None:
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        # check_same_thread=False because the HTTP server handles requests on worker threads.
        # Constraint: the serving process only READS this store; ingestion writes from a separate
        # process (see docs/refresh-job.md). Do not run ingest concurrently on a shared connection.
        self.conn = sqlite3.connect(str(path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.executescript(SCHEMA)

    def close(self) -> None:
        self.conn.close()

    # -- embedder guard ---------------------------------------------------
    def check_embedder(self, embedder: Embedder) -> None:
        tag = f"{embedder.name}:{embedder.dim}"
        row = self.conn.execute("SELECT value FROM meta WHERE key='embedder'").fetchone()
        if row is None:
            with self.conn:
                self.conn.execute("INSERT INTO meta(key,value) VALUES('embedder',?)", (tag,))
        elif row["value"] != tag:
            raise EmbedderMismatchError(f"index built with {row['value']}, got {tag}; rebuild")

    # -- writes -----------------------------------------------------------
    def record_status(self, url: str, content_hash: str) -> str:
        row = self.conn.execute("SELECT content_hash FROM records WHERE url=?", (url,)).fetchone()
        if row is None:
            return "missing"
        return "same" if row["content_hash"] == content_hash else "changed"

    def touch(self, url: str, fetched_at: str) -> None:
        with self.conn:
            self.conn.execute("UPDATE records SET fetched_at=? WHERE url=?", (fetched_at, url))

    def replace_record(self, rec: SourceRecord, chunks: list[str], vectors: np.ndarray) -> None:
        if len(chunks) != vectors.shape[0]:
            raise ValueError("chunks/vectors length mismatch")
        with self.conn:
            old = [r["chunk_id"] for r in self.conn.execute(
                "SELECT chunk_id FROM chunks WHERE url=?", (rec.url,))]
            for cid in old:
                self.conn.execute("DELETE FROM chunks_fts WHERE rowid=?", (cid,))
            self.conn.execute("DELETE FROM chunks WHERE url=?", (rec.url,))
            self.conn.execute(
                """INSERT INTO records(url,source_id,product,title,summary,resource_type,
                   last_updated,source,source_url,fetched_at,content_hash)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(url) DO UPDATE SET source_id=excluded.source_id,
                   product=excluded.product, title=excluded.title, summary=excluded.summary,
                   resource_type=excluded.resource_type, last_updated=excluded.last_updated,
                   source=excluded.source, source_url=excluded.source_url,
                   fetched_at=excluded.fetched_at, content_hash=excluded.content_hash""",
                (rec.url, rec.source_id, rec.product, rec.title, rec.summary, rec.resource_type,
                 rec.last_updated, rec.source, rec.source_url, rec.fetched_at, rec.content_hash),
            )
            for i, (text, vec) in enumerate(zip(chunks, vectors, strict=True)):
                cur = self.conn.execute(
                    "INSERT INTO chunks(url,ord,text,embedding) VALUES(?,?,?,?)",
                    (rec.url, i, text, vec.astype(np.float32).tobytes()),
                )
                self.conn.execute(
                    "INSERT INTO chunks_fts(rowid,text,title,product) VALUES(?,?,?,?)",
                    (cur.lastrowid, text, rec.title, rec.product or ""),
                )

    # -- reads ------------------------------------------------------------
    def count_records(self) -> int:
        return int(self.conn.execute("SELECT COUNT(*) c FROM records").fetchone()["c"])

    def count_chunks(self) -> int:
        return int(self.conn.execute("SELECT COUNT(*) c FROM chunks").fetchone()["c"])

    def get_record(self, url: str) -> dict[str, Any] | None:
        row = self.conn.execute("SELECT * FROM records WHERE url=?", (url,)).fetchone()
        return dict(row) if row else None

    def all_urls(self, source: str | None = None) -> set[str]:
        if source is None:
            rows = self.conn.execute("SELECT url FROM records").fetchall()
        else:
            rows = self.conn.execute("SELECT url FROM records WHERE source=?", (source,)).fetchall()
        return {r["url"] for r in rows}

    def delete_records(self, urls: set[str]) -> int:
        """Remove records and everything derived from them (chunks, FTS rows)."""
        removed = 0
        with self.conn:
            for url in urls:
                for r in self.conn.execute("SELECT chunk_id FROM chunks WHERE url=?", (url,)).fetchall():
                    self.conn.execute("DELETE FROM chunks_fts WHERE rowid=?", (r["chunk_id"],))
                self.conn.execute("DELETE FROM chunks WHERE url=?", (url,))
                removed += self.conn.execute("DELETE FROM records WHERE url=?", (url,)).rowcount
        return removed

    def records_by_product(self, name: str, limit: int = 8) -> list[dict[str, Any]]:
        like = "%" + name.lower().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        rows = self.conn.execute(
            "SELECT * FROM records WHERE lower(product) LIKE ? ESCAPE '\\' ORDER BY title LIMIT ?",
            (like, limit),
        ).fetchall()
        return [dict(r) for r in rows]

    def list_products(self) -> list[tuple[str, int]]:
        rows = self.conn.execute(
            "SELECT COALESCE(product,'(unspecified)') p, COUNT(*) c FROM records GROUP BY p ORDER BY p"
        ).fetchall()
        return [(r["p"], int(r["c"])) for r in rows]

    def fts_search(self, match_query: str, limit: int) -> list[tuple[int, float]]:
        rows = self.conn.execute(
            """SELECT rowid, bm25(chunks_fts, 1.0, 2.0, 2.0) AS score
               FROM chunks_fts WHERE chunks_fts MATCH ? ORDER BY score LIMIT ?""",
            (match_query, limit),
        ).fetchall()
        return [(int(r["rowid"]), float(r["score"])) for r in rows]

    def load_vectors(self) -> tuple[np.ndarray, np.ndarray]:
        rows = self.conn.execute("SELECT chunk_id, embedding FROM chunks ORDER BY chunk_id").fetchall()
        if not rows:
            return np.zeros((0,), dtype=np.int64), np.zeros((0, 0), dtype=np.float32)
        ids = np.array([r["chunk_id"] for r in rows], dtype=np.int64)
        mat = np.vstack([np.frombuffer(r["embedding"], dtype=np.float32) for r in rows])
        return ids, mat

    def get_chunks(self, chunk_ids: list[int]) -> dict[int, dict[str, Any]]:
        if not chunk_ids:
            return {}
        marks = ",".join("?" * len(chunk_ids))
        rows = self.conn.execute(
            f"""SELECT c.chunk_id, c.text, r.url, r.title, r.product, r.resource_type,
                       r.last_updated, r.fetched_at
                FROM chunks c JOIN records r ON r.url=c.url WHERE c.chunk_id IN ({marks})""",
            chunk_ids,
        ).fetchall()
        return {int(r["chunk_id"]): dict(r) for r in rows}
