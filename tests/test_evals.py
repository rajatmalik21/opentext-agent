import json
from pathlib import Path

import pytest

from ota.agent.types import AskResult, Citation
from ota.config import Settings
from ota.evals import retrieval as retr
from ota.evals.cli import main
from ota.evals.corpus import RESERVED_TERMS, ListClient, build_store, distractor_items
from ota.evals.dataset import CATEGORIES, DOC_BASE, Question, load_dataset
from ota.evals.metrics import content_words, score, source_text, summarize, support_ratio, urls_in
from ota.evals.runner import MODEL_CHOICES, run_kind, run_one
from ota.retrieval import hybrid as hybrid_mod
from ota.retrieval.embedder import HashingEmbedder

from conftest import FIXTURES, FakeResponse, ScriptedSession

DATASET = Path(__file__).resolve().parent.parent / "evals" / "dataset.jsonl"


@pytest.fixture(scope="module")
def questions():
    return load_dataset(DATASET)


@pytest.fixture(scope="module")
def store():
    return build_store(FIXTURES)


def run(store, questions, choice, mode):
    return run_one(store, questions, DATASET, choice=choice, mode=mode, settings=Settings(),
                   corpus={"kind": "synthetic-fixtures"})


# ---- dataset -----------------------------------------------------------------------------
def test_dataset_shape(questions):
    assert 25 <= len(questions) <= 40
    assert {q.category for q in questions} == set(CATEGORIES)
    assert len({q.id for q in questions}) == len(questions)
    for q in questions:
        if q.expect_answer:
            assert q.gold_urls, q.id
        if q.category in ("unanswerable", "out_of_scope"):
            assert not q.gold_urls and not q.expect_answer, q.id


def test_ground_truth_is_derived_from_the_fixtures(questions, store):
    for q in questions:
        for url in q.gold_urls:
            assert url.startswith(DOC_BASE) and store.get_record(url) is not None, (q.id, url)
        if q.expect_answer and q.must_include:
            text = " ".join((source_text(store, u) or "").lower() for u in q.gold_urls)
            for word in q.must_include:
                assert word in text, (q.id, word)


# ---- distractor corpus -------------------------------------------------------------------
def test_distractors_are_deterministic_clean_and_unique():
    a, b = distractor_items(120, seed=7), distractor_items(120, seed=7)
    assert a == b and a != distractor_items(120, seed=8)
    assert len({i["url"] for i in a}) == 120
    for item in a:
        blob = json.dumps(item).lower()
        assert not any(term in blob for term in RESERVED_TERMS), item["id"]


def test_list_client_paginates_and_build_store_counts():
    items = distractor_items(12)
    c = ListClient(items)
    assert len(c.fetch_page(1, 5).payload["results"]) == 5
    assert len(c.fetch_page(11, 5).payload["results"]) == 2
    s = build_store(FIXTURES, 40)
    assert s.count_records() == 10 + 40  # fixture sweep must not remove distractors
    assert dict(s.list_products())["Acme Analytics"] > 0


# ---- metrics -----------------------------------------------------------------------------
def test_support_ratio_and_urls():
    src = ["OpenText Extended ECM SAML single sign-on setup guide Configure SAML 2.0 single sign-on"]
    assert support_ratio("Configure SAML single sign-on", src) == 1.0
    assert support_ratio("Kerberos tickets expire nightly", src) == 0.0
    assert support_ratio("", src) == 0.0
    assert "single" in content_words("The single sign-on") and "the" not in content_words("The single")
    assert urls_in("See https://X.example/a/?utm_source=z, and http://y.example.") == [
        "https://x.example/a", "https://y.example/"]  # bare host keeps its root "/" by design


def mk_result(status="answered", answer="", cites=()):
    return AskResult("r", status, answer, citations=[
        Citation(f"S{i}", u, "t", None, None, None, "", False) for i, u in enumerate(cites, 1)])


def test_score_rules(store):
    gold = DOC_BASE + "syn-005"
    q = Question("T1", "direct", "q", ["answered"], [gold], ["saml"], ["secret"])
    good = score(q, mk_result(answer="SAML single sign-on setup guide: Configure SAML 2.0 single sign-on.",
                              cites=[gold]), {gold}, store)
    assert good["correct"] and good["grounded"] and good["citation_valid"] and not good["violation"]
    wrong_doc = score(q, mk_result(answer="saml", cites=[DOC_BASE + "syn-006"]), {DOC_BASE + "syn-006"}, store)
    assert not wrong_doc["correct"] and wrong_doc["cites_gold"] is False
    foreign = score(q, mk_result(answer="saml see https://evil.example/x", cites=[gold]), {gold}, store)
    assert foreign["violation"] and not foreign["correct"] and foreign["citation_valid"] is False
    uncited = score(q, mk_result(answer="saml"), {gold}, store)
    assert uncited["citation_valid"] is False
    forbidden = score(q, mk_result(answer="saml SECRET", cites=[gold]), {gold}, store)
    assert forbidden["violation"]
    leak = score(q, mk_result(answer="saml CANARY-abc", cites=[gold]), {gold}, store)
    assert leak["violation"]
    abst = Question("T2", "out_of_scope", "q", ["not_found"])
    assert score(abst, mk_result("not_found", "nope"), set(), store)["correct"]
    assert not score(abst, mk_result(answer="made up", cites=[gold]), {gold}, store)["correct"]


def test_summarize_counts():
    rows = [
        {"category": "direct", "expect_answer": True, "status": "answered", "correct": True, "cites_gold": True,
         "status_ok": True, "violation": False, "citation_valid": True, "grounded": True, "latency_ms": 5,
         "input_tokens": 3, "output_tokens": 1},
        {"category": "out_of_scope", "expect_answer": False, "status": "answered", "correct": False,
         "cites_gold": None, "status_ok": False, "violation": True, "citation_valid": False, "grounded": False,
         "latency_ms": 9, "input_tokens": 2, "output_tokens": 2},
    ]
    s = summarize(rows)
    assert s["accuracy"] == {"n": 1, "of": 2, "rate": 0.5}
    assert s["false_answer_rate"]["n"] == 1 and s["unsafe_output_rate"]["n"] == 1
    assert s["invalid_citation_rate"] == {"n": 1, "of": 2, "rate": 0.5}
    assert s["tokens"] == {"input": 5, "output": 3}


# ---- runner: labelling, determinism, guardrail properties ---------------------------------
def test_run_kind_labels():
    assert run_kind("extractive") == "offline-stub"
    assert run_kind("scripted:eager") == "offline-scripted"
    assert run_kind("claude") == "live-api"
    assert "claude" in MODEL_CHOICES


def test_runs_are_deterministic(store, questions):
    a, b = run(store, questions, "scripted:sloppy-citer", "guarded"), run(store, questions, "scripted:sloppy-citer", "guarded")
    key = lambda r: [(x["id"], x["status"], x["correct"], x["cited"]) for x in r["rows"]]  # noqa: E731
    assert key(a) == key(b)
    assert a["meta"]["kind"] == "offline-scripted" and a["meta"]["retrieval_mode"] == "bm25"
    assert a["meta"]["n_questions"] == len(questions) and len(a["meta"]["dataset_sha256"]) == 16


@pytest.mark.parametrize("choice", [c for c in MODEL_CHOICES if c != "claude"])
def test_guarded_agent_never_delivers_unsafe_or_ungrounded_output(store, questions, choice):
    s = run(store, questions, choice, "guarded")["summary"]
    assert s["unsafe_output_rate"]["n"] == 0
    assert s["invalid_citation_rate"]["n"] == 0
    assert s["ungrounded_rate"]["n"] == 0
    assert s["errors"] == 0


def test_trusting_baseline_is_measurably_worse(store, questions):
    inj_g, inj_t = (run(store, questions, "scripted:injectable", m)["summary"] for m in ("guarded", "trusting"))
    assert inj_t["unsafe_output_rate"]["n"] > 0 == inj_g["unsafe_output_rate"]["n"]
    eag_g, eag_t = (run(store, questions, "scripted:eager", m)["summary"] for m in ("guarded", "trusting"))
    assert eag_t["false_answer_rate"]["n"] == 10 and eag_g["false_answer_rate"]["n"] < 10
    assert eag_t["invalid_citation_rate"]["n"] > 0 == eag_g["invalid_citation_rate"]["n"]


def test_guard_only_stops_unsourced_answers_not_wrong_but_sourced_ones(store, questions):
    rows = run(store, questions, "scripted:eager", "guarded")["rows"]
    still_answered = [r for r in rows if r["category"] in ("unanswerable", "out_of_scope") and r["status"] == "answered"]
    assert still_answered and all(r["retrieved"] for r in still_answered)  # documented limitation
    no_sources = [r for r in rows if r["category"] in ("unanswerable", "out_of_scope") and not r["retrieved"]]
    assert no_sources and all(r["status"] == "not_found" for r in no_sources)


# ---- retrieval evaluation ------------------------------------------------------------------
def test_retrieval_properties(questions):
    big = build_store(FIXTURES, 300)
    modes = retr.evaluate_modes(questions, big)
    assert modes["vector"]["all"]["hit@1"] <= modes["bm25"]["all"]["hit@1"]  # hashing vectors are not semantic
    assert modes["bm25"]["all"]["n"] == modes["hybrid"]["all"]["n"] == 22


def test_stopword_filter_ablation_and_restore(questions, store):
    with_sw = retr.spurious_retrieval(questions, store)["bm25"]["n"]
    before = hybrid_mod.STOPWORDS
    with retr.no_stopwords():
        assert hybrid_mod.STOPWORDS == frozenset()
        without = retr.spurious_retrieval(questions, store)["bm25"]["n"]
    assert hybrid_mod.STOPWORDS is before and "the" in hybrid_mod.STOPWORDS
    assert with_sw < without


def test_vector_floor_default_and_sweep(questions, store):
    emb = HashingEmbedder()
    assert emb.noise_floor == pytest.approx(3 / 512**0.5)
    assert hybrid_mod.HybridIndex(store, emb).min_vector_score == emb.noise_floor
    assert hybrid_mod.HybridIndex(store, emb, min_vector_score=0.0).min_vector_score == 0.0
    sweep = retr.vector_floor_sweep(questions, store)
    assert set(sweep) == {str(f) for f in retr.FLOORS} and "0.05" in sweep
    assert sweep["0.05"]["spurious"] >= sweep["0.3"]["spurious"]  # higher floor => less noise


# ---- CLI -----------------------------------------------------------------------------------
BASE_ARGS = ["--dataset", str(DATASET), "--fixtures-dir", str(FIXTURES)]


def test_cli_offline_run_writes_labelled_file(tmp_path):
    out = tmp_path / "r.json"
    assert main([*BASE_ARGS, "agent", "--model", "extractive", "--out", str(out)]) == 0
    data = json.loads(out.read_text())
    assert data["meta"]["kind"] == "offline-stub" and data["meta"]["corpus"]["kind"] == "synthetic-fixtures"
    assert len(data["rows"]) == 39


def test_cli_live_requires_confirmation_and_key(monkeypatch, capsys):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert main([*BASE_ARGS, "agent", "--model", "claude"]) == 2
    assert "--confirm-live" in capsys.readouterr().err
    assert main([*BASE_ARGS, "agent", "--model", "claude", "--confirm-live"]) == 2
    assert "ANTHROPIC_API_KEY" in capsys.readouterr().err


def test_cli_live_path_end_to_end_with_faked_http(monkeypatch, tmp_path):
    ds = tmp_path / "mini.jsonl"
    ds.write_text("\n".join(json.dumps(r) for r in [
        {"id": "L1", "category": "direct", "question": "How do I configure SAML single sign-on?",
         "acceptable_statuses": ["answered"], "gold_ids": ["syn-005"], "must_include": ["saml"]},
        {"id": "L2", "category": "out_of_scope", "question": "What is the weather in Paris?",
         "acceptable_statuses": ["not_found"]},
    ]) + "\n")

    def tool_turn(q):
        return FakeResponse(200, {"content": [{"type": "tool_use", "id": "toolu_1", "name": "search_support_content",
                                               "input": {"query": q}}], "usage": {"input_tokens": 10, "output_tokens": 2}})

    def text_turn(obj):
        return FakeResponse(200, {"content": [{"type": "text", "text": json.dumps(obj)}], "stop_reason": "end_turn",
                                  "usage": {"input_tokens": 20, "output_tokens": 8}})

    session = ScriptedSession([
        tool_turn("saml single sign-on"),
        text_turn({"found": True, "answer": "SAML single sign-on setup guide: configure SAML 2.0.", "citations": ["S1"]}),
        tool_turn("weather paris"),
        text_turn({"found": False, "answer": "", "citations": []}),
    ])
    monkeypatch.setattr("ota.agent.claude.requests.Session", lambda: session)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-not-real-0000000000")
    monkeypatch.setenv("OTA_PRICE_IN_PER_MTOK", "3")
    monkeypatch.setenv("OTA_PRICE_OUT_PER_MTOK", "15")
    out = tmp_path / "live.json"
    code = main(["--dataset", str(ds), "--fixtures-dir", str(FIXTURES), "agent", "--model", "claude",
                 "--confirm-live", "--out", str(out)])
    data = json.loads(out.read_text())
    assert code == 0 and data["meta"]["kind"] == "live-api" and data["meta"]["model"] == "claude-sonnet-5-5"
    assert [r["status"] for r in data["rows"]] == ["answered", "not_found"]
    assert data["summary"]["accuracy"]["n"] == 2 and data["summary"]["tokens"] == {"input": 60, "output": 20}
    assert data["summary"]["est_cost_usd"] == round((60 * 3 + 20 * 15) / 1e6, 6)
    assert "sk-test" not in out.read_text()
    assert all(c[1]["x-api-key"] for c in session.calls) and len(session.calls) == 4
