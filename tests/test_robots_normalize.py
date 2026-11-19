import pytest

from ota.ingest.normalize import (
    SchemaDriftError, canonical_url, clean_text, extract_items, extract_total, normalize_item,
)
from ota.ingest.robots import RobotsRules

from conftest import load

API = "/api/web/support/search-results?start=1&maxResults=5&text=*"


def test_robots_real_file_allows_api_and_blocks_referrer(robots_text):
    r = RobotsRules.parse(robots_text, "ota-assessment-agent/0.1")
    assert r.allowed(API)
    assert r.allowed("/support/products/")
    assert not r.allowed("/support/products?x=1&referrer=abc")
    assert not r.allowed("/api/x?referrer=1")


def test_robots_specific_agent_group_wins():
    txt = "User-agent: *\nAllow: /\n\nUser-agent: ota-bot\nDisallow: /\n"
    assert not RobotsRules.parse(txt, "ota-bot/1.0").allowed("/a")
    assert RobotsRules.parse(txt, "other").allowed("/a")


def test_robots_empty_disallow_and_anchor():
    assert RobotsRules.parse("User-agent: *\nDisallow:\n").allowed("/x")
    r = RobotsRules.parse("User-agent: *\nDisallow: /*.pdf$\n")
    assert not r.allowed("/a/b.pdf") and r.allowed("/a/b.pdf?x=1")


def test_robots_longest_match_allow_wins_tie():
    r = RobotsRules.parse("User-agent: *\nDisallow: /a\nAllow: /a/b\n")
    assert r.allowed("/a/b/c") and not r.allowed("/a/x")


@pytest.mark.parametrize("raw,expected", [
    ("/support/x/", "https://my.opentext.com/support/x"),
    ("/support/x?utm_source=a&id=2#frag", "https://my.opentext.com/support/x?id=2"),
    ("HTTP://MY.OPENTEXT.COM/A?referrer=z", "https://my.opentext.com/A"),
    ("javascript:alert(1)", None),
    ("", None),
])
def test_canonical_url(raw, expected):
    assert canonical_url(raw) == expected


def test_clean_text_strips_html_and_scripts():
    assert clean_text("a <b>b</b> &amp; c<script>evil()</script> d") == "a b & c d"


def test_extract_items_variants_and_drift_error():
    assert len(extract_items(load("synthetic_search_p1.json"))) == 5
    drift = load("synthetic_search_drift.json")
    assert len(extract_items(drift)) == 2 and extract_total(drift) == 2
    assert extract_items({"total": 0}) == []
    with pytest.raises(SchemaDriftError):
        extract_items({"unexpected": "shape"})
    with pytest.raises(SchemaDriftError):
        extract_items("garbage")


def test_normalize_drifted_keys_and_rejects():
    items = extract_items(load("synthetic_search_drift.json"))
    recs = [normalize_item(i, source="t", source_url="u", fetched_at="now") for i in items]
    assert recs[0].title == "Drifted field names A"
    assert recs[0].product == "OpenText Content Cloud" and recs[0].resource_type == "FAQ"
    assert recs[1].product == "OpenText Service Management"  # dict-valued product handled
    assert recs[1].url == "https://fixtures.invalid/synthetic/drift-b"
    assert normalize_item({"title": "no url"}, source="t", source_url="u", fetched_at="n") is None
    assert normalize_item({"url": "/x"}, source="t", source_url="u", fetched_at="n") is None


def test_content_hash_ignores_fetch_time():
    item = {"title": "T", "url": "/x", "description": "d"}
    a = normalize_item(item, source="s", source_url="u", fetched_at="1")
    b = normalize_item(item, source="s", source_url="u", fetched_at="2")
    c = normalize_item({**item, "description": "d2"}, source="s", source_url="u", fetched_at="1")
    assert a.content_hash == b.content_hash != c.content_hash
