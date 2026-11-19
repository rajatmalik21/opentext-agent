import pytest

from ota.chunking import build_chunks
from ota.ingest.client import FixtureClient, SourceAccessError, FetchResult
from ota.ingest.pipeline import ingest
from ota.models import SourceRecord
from ota.retrieval.embedder import HashingEmbedder
from ota.retrieval.hybrid import HybridIndex, fts_query
from ota.store.sqlite_store import EmbedderMismatchError, SQLiteStore

from conftest import load

EMB = HashingEmbedder()


@pytest.fixture
def built(fixtures_dir):
    store = SQLiteStore()
    report = ingest(FixtureClient(fixtures_dir), store, EMB)
    return store, report


def test_full_ingest_counts(built):
    store, r = built
    assert r.complete and r.stop_reason == "reached_total"
    assert (r.pages_fetched, r.items_seen) == (3, 12)
    assert (r.rejected, r.duplicates, r.inserted) == (1, 1, 10)
    assert store.count_records() == 10 and store.count_chunks() == 10
    assert dict(store.list_products()) == {
        "OpenText Content Cloud": 4, "OpenText Extended ECM": 3, "OpenText Service Management": 3}


def test_idempotent_rerun_and_change_detection(built, fixtures_dir):
    store, _ = built
    r2 = ingest(FixtureClient(fixtures_dir), store, EMB)
    assert (r2.inserted, r2.updated, r2.unchanged) == (0, 0, 10)
    assert store.count_chunks() == 10

    class Changed(FixtureClient):
        def fetch_page(self, start, max_results, text="*"):
            res = super().fetch_page(start, max_results, text)
            if start == 1:
                res.payload["results"][0]["description"] = "Totally new upgrade text"
            return res

    r3 = ingest(Changed(fixtures_dir), store, EMB)
    assert (r3.updated, r3.unchanged, r3.inserted) == (1, 9, 0)
    assert store.count_chunks() == 10  # old chunks removed, not leaked
    hits = HybridIndex(store, EMB).search("totally new upgrade text", k=1)
    assert hits[0].title == "Upgrade guide for version 24.2"


def test_provenance_stored(built):
    store, _ = built
    rec = store.get_record("https://fixtures.invalid/synthetic/syn-005")
    assert rec["source"] == "support-search-api" and rec["fetched_at"]
    assert len(rec["content_hash"]) == 64 and rec["source_url"].startswith("fixture://")


def test_html_and_script_stripped_but_injection_text_kept_as_data(built):
    store, _ = built
    rec = store.get_record("https://fixtures.invalid/synthetic/syn-010")
    assert "<script" not in rec["summary"] and "alert(1)" not in rec["summary"]
    assert "IGNORE ALL PREVIOUS INSTRUCTIONS" in rec["summary"]  # untrusted data, handled in Phase 2


class Scripted:
    def __init__(self, pages):
        self.pages = pages

    def fetch_page(self, start, max_results, text="*"):
        p = self.pages[start]
        if isinstance(p, Exception):
            raise p
        return FetchResult(f"scripted://{start}", p, False)


def test_first_page_failure_raises():
    with pytest.raises(SourceAccessError):
        ingest(Scripted({1: SourceAccessError("403")}), SQLiteStore(), EMB)


def test_later_page_failure_keeps_partial_and_reports():
    pages = {1: load("synthetic_search_p1.json"), 6: SourceAccessError("blocked")}
    store = SQLiteStore()
    r = ingest(Scripted(pages), store, EMB)
    assert not r.complete and r.stop_reason == "error" and r.inserted == 5
    assert "SourceAccessError" in r.errors[0]


def test_schema_drift_midway_is_reported():
    pages = {1: load("synthetic_search_p1.json"), 6: {"weird": True}}
    r = ingest(Scripted(pages), SQLiteStore(), EMB)
    assert r.stop_reason == "error" and "SchemaDriftError" in r.errors[0]


def test_empty_first_page_and_max_pages():
    r = ingest(Scripted({1: load("synthetic_search_empty.json")}), SQLiteStore(), EMB)
    assert r.complete and r.stop_reason == "empty_page" and r.inserted == 0
    r2 = ingest(FixtureClient(load.__globals__["FIXTURES"]), SQLiteStore(), EMB, max_pages=1)
    assert r2.stop_reason == "max_pages" and not r2.complete and r2.inserted == 5


def test_embedder_mismatch_is_refused(built):
    store, _ = built
    with pytest.raises(EmbedderMismatchError):
        store.check_embedder(HashingEmbedder(dim=64))


# ---- retrieval -------------------------------------------------------------
@pytest.mark.parametrize("query,expected_title", [
    ("how do I configure SAML single sign-on", "SAML single sign-on setup guide"),
    ("LDAP directory sync problems", "Troubleshooting LDAP sync failures"),
    ("email to ticket mailbox connector", "Configure email-to-ticket integration"),
    ("backup and restore", "Backup and restore best practices"),
    ("when does 22.x support end", "End of support dates for 22.x"),
])
def test_hybrid_top1(built, query, expected_title):
    store, _ = built
    assert HybridIndex(store, EMB).search(query, k=3)[0].title == expected_title


def test_modes_and_dedup_per_url(built):
    store, _ = built
    idx = HybridIndex(store, EMB)
    for mode in ("hybrid", "bm25", "vector"):
        hits = idx.search("search indexing stalls patch", k=5, mode=mode)
        assert hits and len({h.url for h in hits}) == len(hits)
        assert hits[0].url.endswith("syn-002")
    h = idx.search("search indexing stalls patch", mode="hybrid")[0]
    assert h.bm25_rank == 1


def test_no_match_and_empty_query(built):
    store, _ = built
    idx = HybridIndex(store, EMB)
    assert idx.search("???", k=3) == []
    assert idx.search("zzzxqv unrelatedgibberish", k=3, mode="bm25") == []


def test_fts_query_is_injection_safe(built):
    store, _ = built
    # operators become quoted literals; stopwords ("or") and 1-char tokens ("x") are dropped
    assert fts_query('foo" OR bar* NEAR(x)') == '"foo" OR "bar" OR "near"'
    assert fts_query("what is the weather in") == '"weather"'
    assert fts_query("what is the") is None
    HybridIndex(store, EMB).search('") OR title:* --', k=3)  # must not raise


def test_chunking_windows_long_text():
    rec = SourceRecord(source_id="1", title="T", url="https://x/y", product="P",
                       summary=" ".join(f"w{i}" for i in range(400)), source="s",
                       source_url="u", fetched_at="n", content_hash="h")
    chunks = build_chunks(rec, max_words=180, overlap=30)
    assert len(chunks) == 3 and all(c.startswith("P | T") for c in chunks)
    # step = 180-30 = 150, so words w150..w179 appear in both chunk 0 and chunk 1
    assert "w150" in chunks[0] and "w150" in chunks[1] and "w149" not in chunks[1]
    assert "w399" in chunks[-1]


def test_hashing_embedder_is_deterministic_and_normalized():
    a = EMB.embed(["hello world", ""])
    b = EMB.embed(["hello world", ""])
    assert (a == b).all()
    assert abs(float((a[0] ** 2).sum()) - 1.0) < 1e-5 and float(abs(a[1]).sum()) == 0.0
