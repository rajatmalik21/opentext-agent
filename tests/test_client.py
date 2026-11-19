import pytest

from ota.ingest.client import (
    ClientConfig, SearchClient, SourceAccessError, SourceUnavailableError,
)

from conftest import CONNERR, TIMEOUT, FakeResponse, ScriptedSession

OK = {"total": 1, "results": [{"title": "T", "url": "/x"}]}


def make(script, robots="User-agent: *\nAllow: /\n", **cfg):
    sleeps = []
    session = ScriptedSession(script)
    client = SearchClient(
        ClientConfig(robots_txt=robots, **cfg), session,
        sleep=sleeps.append, clock=lambda: 1000.0, jitter=lambda: 0.0,
    )
    return client, session, sleeps


def test_success_sets_user_agent_and_url():
    client, session, _ = make([FakeResponse(200, OK)])
    res = client.fetch_page(1, 5)
    assert res.payload == OK and not res.from_cache
    url, headers, timeout = session.calls[0]
    assert url.endswith("/api/web/support/search-results?start=1&maxResults=5&text=*")
    assert headers["User-Agent"].startswith("ota-assessment-agent/")
    assert timeout == 15.0


def test_retries_transient_then_succeeds_with_exponential_backoff():
    # min_interval_s=0 so the only sleeps are backoff sleeps (throttling is tested separately)
    client, session, sleeps = make(
        [TIMEOUT, CONNERR, FakeResponse(503), FakeResponse(200, OK)], min_interval_s=0)
    assert client.fetch_page(1, 5).payload == OK
    assert len(session.calls) == 4
    assert sleeps == [0.5, 1.0, 2.0]


def test_429_honours_retry_after_capped():
    client, _, sleeps = make(
        [FakeResponse(429, headers={"Retry-After": "7"}), FakeResponse(200, OK)])
    client.fetch_page(1, 5)
    assert 7.0 in sleeps
    client2, _, sleeps2 = make(
        [FakeResponse(429, headers={"Retry-After": "9999"}), FakeResponse(200, OK)])
    client2.fetch_page(1, 5)
    assert 60.0 in sleeps2


def test_gives_up_after_max_retries():
    client, session, _ = make([FakeResponse(500)] * 3, max_retries=2)
    with pytest.raises(SourceUnavailableError):
        client.fetch_page(1, 5)
    assert len(session.calls) == 3


@pytest.mark.parametrize("code", [400, 401, 403, 404])
def test_4xx_is_not_retried_or_bypassed(code):
    client, session, _ = make([FakeResponse(code), FakeResponse(200, OK)])
    with pytest.raises(SourceAccessError, match=str(code)):
        client.fetch_page(1, 5)
    assert len(session.calls) == 1


def test_non_json_is_access_error():
    client, _, _ = make([FakeResponse(200, bad_json=True)])
    with pytest.raises(SourceAccessError, match="not JSON"):
        client.fetch_page(1, 5)


def test_robots_block_prevents_any_request():
    client, session, _ = make([], robots="User-agent: *\nDisallow: /api/\n")
    with pytest.raises(SourceAccessError, match="robots"):
        client.fetch_page(1, 5)
    assert session.calls == []


def test_robots_fetched_once_live_and_404_means_allow_all():
    client, session, _ = make(
        [FakeResponse(404), FakeResponse(200, OK), FakeResponse(200, OK)], robots=None)
    client.fetch_page(1, 5)
    client.fetch_page(6, 5)
    assert [c[0].split("/")[-1] for c in session.calls][0] == "robots.txt"
    assert sum("robots.txt" in c[0] for c in session.calls) == 1


def test_rate_limit_spacing():
    sleeps = []
    t = [1000.0]
    client = SearchClient(
        ClientConfig(robots_txt="", min_interval_s=1.0),
        ScriptedSession([FakeResponse(200, OK), FakeResponse(200, OK)]),
        sleep=sleeps.append, clock=lambda: t[0], jitter=lambda: 0.0,
    )
    client.fetch_page(1, 5)
    t[0] += 0.25
    client.fetch_page(6, 5)
    assert sleeps == [0.75]


def test_cache_hit_and_ttl(tmp_path):
    now = [100.0]
    session = ScriptedSession([FakeResponse(200, OK), FakeResponse(200, OK)])
    client = SearchClient(
        ClientConfig(robots_txt="", cache_dir=tmp_path, cache_ttl_s=60), session,
        sleep=lambda s: None, clock=lambda: 0.0, wall_clock=lambda: now[0], jitter=lambda: 0.0,
    )
    assert not client.fetch_page(1, 5).from_cache
    assert client.fetch_page(1, 5).from_cache
    now[0] += 120
    assert not client.fetch_page(1, 5).from_cache
    assert len(session.calls) == 2
