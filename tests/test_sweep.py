import pytest

from ota.ingest.client import FetchResult, FixtureClient
from ota.ingest.pipeline import ingest
from ota.retrieval.embedder import HashingEmbedder
from ota.retrieval.hybrid import HybridIndex
from ota.store.sqlite_store import SQLiteStore

from conftest import load

EMB = HashingEmbedder()


class Without(FixtureClient):
    """Fixture source with some upstream items deleted (ids given)."""

    def __init__(self, directory, drop):
        super().__init__(directory)
        self.drop = set(drop)

    def fetch_page(self, start, max_results, text="*"):
        res = super().fetch_page(start, max_results, text)
        res.payload = {**res.payload, "results": [i for i in res.payload["results"] if i["id"] not in self.drop]}
        return res


class Fixed:
    def __init__(self, payload):
        self.payload = payload

    def fetch_page(self, start, max_results, text="*"):
        return FetchResult("fixed://", self.payload, False)


@pytest.fixture
def store(fixtures_dir):
    s = SQLiteStore()
    ingest(FixtureClient(fixtures_dir), s, EMB)
    return s


def test_record_deleted_upstream_is_removed_everywhere(store, fixtures_dir):
    r = ingest(Without(fixtures_dir, {"syn-012"}), store, EMB)
    assert r.complete and r.removed == 1 and "removed 1" in r.sweep_note
    assert store.count_records() == 9 and store.count_chunks() == 9
    assert store.get_record("https://fixtures.invalid/synthetic/syn-012") is None
    hits = HybridIndex(store, EMB).search("customer portal licensing FAQ", k=5, mode="bm25")
    assert all(not h.url.endswith("syn-012") for h in hits)  # FTS row gone too
    assert store.conn.execute("SELECT COUNT(*) FROM chunks_fts").fetchone()[0] == 9


def test_mass_removal_guard_protects_index_from_empty_upstream(store):
    r = ingest(Fixed({"total": 0, "results": []}), store, EMB)
    assert r.complete and r.removed == 0 and "over guard" in r.sweep_note
    assert store.count_records() == 10
    r2 = ingest(Fixed(load("synthetic_search_drift.json")), store, EMB)  # 2 records, 8 would vanish
    assert r2.removed == 0 and "over guard" in r2.sweep_note and store.count_records() == 12


def test_incomplete_run_never_sweeps(store, fixtures_dir):
    r = ingest(Without(fixtures_dir, {"syn-012", "syn-011"}), store, EMB, max_pages=1)
    assert not r.complete and r.removed == 0 and r.sweep_note == "skipped: run incomplete"
    assert store.count_records() == 10


def test_sweep_is_scoped_to_its_source_and_can_be_disabled(store, fixtures_dir):
    ingest(Fixed(load("synthetic_search_drift.json")), store, EMB, source="other-source", sweep=False)
    assert store.count_records() == 12
    r = ingest(Without(fixtures_dir, {"syn-012"}), store, EMB)
    assert r.removed == 1 and store.count_records() == 11  # other-source's 2 records untouched
    # Second run drops syn-011 upstream, but sweep is disabled so it must stay. It also
    # re-inserts syn-012 (present upstream again), so 11 + 1 = 12 records.
    r2 = ingest(Without(fixtures_dir, {"syn-011"}), store, EMB, sweep=False)
    assert r2.removed == 0 and r2.inserted == 1 and store.count_records() == 12
    assert store.get_record("https://fixtures.invalid/synthetic/syn-011") is not None
