"""The Gemini client, exercised against a local mock of Google's REST API (no quota).

Covers the reason for the google-genai migration: both key formats ("AIza..." and
the newer "AQ...") must reach Google unchanged in the x-goog-api-key header.
"""
from __future__ import annotations

import pytest
from google.genai import errors as gerrors

from app.config import get_settings
from app.llm import gemini_client as gc
from app.llm.client import describe_error
from app.llm.keyctx import reset_request_key, set_request_key
from mock_gemini import MockGemini, call_response, error_body, text_response

SERVER_KEY = "AQ.server-key-for-tests"


@pytest.fixture
def mock(monkeypatch):
    m = MockGemini(valid_key=SERVER_KEY).start()
    monkeypatch.setenv("GEMINI_BASE_URL", m.url)
    monkeypatch.setenv("GEMINI_API_KEY", SERVER_KEY)
    get_settings.cache_clear()
    yield m
    m.stop()


def _keys_sent(m: MockGemini) -> set[str]:
    return {r["key"] for r in m.requests}


# --------------------------------------------------------------------------- #
# Keys
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("key", ["AQ.Ab8RN6-example_key.123", "AIzaSyExampleKey1234567890"])
def test_both_key_formats_are_sent_unchanged(key, monkeypatch):
    m = MockGemini(valid_key=key).start()
    try:
        monkeypatch.setenv("GEMINI_BASE_URL", m.url)
        monkeypatch.setenv("GEMINI_API_KEY", f"  {key}  ")  # stray whitespace is stripped
        get_settings.cache_clear()
        m.generate_script.append(text_response("ok"))
        assert gc.generate("hi") == "ok"
        assert _keys_sent(m) == {key}
    finally:
        m.stop()


def test_request_key_overrides_server_key(mock):
    mock.valid_key = "AQ.visitor-key"
    token = set_request_key("AQ.visitor-key")
    try:
        gc.validate_key()
    finally:
        reset_request_key(token)
    assert _keys_sent(mock) == {"AQ.visitor-key"}


def test_invalid_key_gives_a_clean_message(mock):
    token = set_request_key("AQ.wrong")
    try:
        with pytest.raises(gerrors.APIError) as ei:
            gc.validate_key()
    finally:
        reset_request_key(token)
    assert ei.value.code == 400
    assert describe_error(ei.value) == "INVALID_ARGUMENT: API key not valid. Please pass a valid API key."


def test_missing_key_explains_what_to_do(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "")
    get_settings.cache_clear()
    with pytest.raises(RuntimeError, match="No Gemini API key"):
        gc.generate("hi")


# --------------------------------------------------------------------------- #
# Embeddings + generation
# --------------------------------------------------------------------------- #
def test_embeddings_keep_dimension_and_task_types(mock):
    vecs = gc.embed_texts(["a", "b", "c"])
    assert len(vecs) == 3 and len(vecs[0]) == 768
    gc.embed_query("where is login?")
    bodies = [r["body"]["requests"] for r in mock.requests if r["path"].endswith(":batchEmbedContents")]
    assert bodies[0][0]["taskType"] == "RETRIEVAL_DOCUMENT"
    assert bodies[1][0]["taskType"] == "RETRIEVAL_QUERY"
    assert bodies[0][0]["outputDimensionality"] == 768
    assert bodies[0][0]["model"] == "models/gemini-embedding-001"


def test_large_embedding_batches_are_split(mock):
    vecs = gc.embed_texts([f"t{i}" for i in range(230)])
    assert len(vecs) == 230
    sizes = [len(r["body"]["requests"]) for r in mock.requests]
    assert sizes == [100, 100, 30]


def test_generate_sends_system_prompt_and_temperature(mock):
    mock.generate_script.append(text_response("hello"))
    assert gc.generate("hi", system="be brief", temperature=0.3) == "hello"
    body = mock.requests[-1]["body"]
    assert body["systemInstruction"]["parts"][0]["text"] == "be brief"
    assert body["generationConfig"]["temperature"] == 0.3


# --------------------------------------------------------------------------- #
# Retry behaviour
# --------------------------------------------------------------------------- #
def test_429_is_retried_after_the_server_suggested_delay(mock, monkeypatch):
    sleeps: list[float] = []
    monkeypatch.setattr("app.llm.gemini_client.time.sleep", sleeps.append)
    mock.generate_script.extend([
        (429, error_body(429, "RESOURCE_EXHAUSTED", "Quota exceeded. Please retry in 2s.",
                         [{"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "2s"}])),
        text_response("recovered"),
    ])
    assert gc.generate("hi") == "recovered"
    assert sleeps == [2.5]  # suggested 2s + the half-second cushion


def test_daily_quota_fails_fast_without_waiting(mock, monkeypatch):
    sleeps: list[float] = []
    monkeypatch.setattr("app.llm.gemini_client.time.sleep", sleeps.append)
    mock.generate_script.append(
        (429, error_body(429, "RESOURCE_EXHAUSTED", "You exceeded your current quota.",
                         [{"violations": [{"quotaId": "GenerateRequestsPerDayPerProjectPerModel-FreeTier"}]}])))
    with pytest.raises(gerrors.APIError):
        gc.generate("hi")
    assert sleeps == []


def test_bad_request_is_not_retried(mock, monkeypatch):
    sleeps: list[float] = []
    monkeypatch.setattr("app.llm.gemini_client.time.sleep", sleeps.append)
    mock.generate_script.append((400, error_body(400, "INVALID_ARGUMENT", "Bad prompt")))
    with pytest.raises(gerrors.APIError):
        gc.generate("hi")
    assert sleeps == [] and sum(r["path"].endswith(":generateContent") for r in mock.requests) == 1


# --------------------------------------------------------------------------- #
# The tool-calling agent, end to end through the real runtime
# --------------------------------------------------------------------------- #
def _repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "orders.py").write_text("def total():\n    return 1\n")
    return repo


def test_agent_runs_tools_and_returns_a_grounded_answer(mock, tmp_path):
    from app.agent.runtime import run_agent

    repo = _repo(tmp_path)
    mock.generate_script.extend([
        call_response("list_files", {"subdir": ""}),
        text_response("The repo has orders.py."),
    ])
    result = run_agent("r", str(repo), "what files are here?")

    assert result.answer == "The repo has orders.py."
    assert result.steps == 1
    assert result.tool_calls[0].name == "list_files"
    assert "orders.py" in result.tool_calls[0].result_preview
    # The tool's real output was fed back to the model on the second request.
    second = [r for r in mock.requests if r["path"].endswith(":generateContent")][1]["body"]
    sent_back = str(second["contents"])
    assert "functionResponse" in sent_back and "orders.py" in sent_back


def test_agent_tool_call_cap_is_enforced(mock, monkeypatch, tmp_path):
    from app.agent.runtime import run_agent

    monkeypatch.setenv("AGENT_MAX_STEPS", "2")
    get_settings.cache_clear()
    repo = _repo(tmp_path)
    mock.generate_script.extend(call_response("list_files", {"subdir": ""}) for _ in range(8))

    result = run_agent("r", str(repo), "keep exploring forever")

    assert result.steps <= 2
    assert "used all 2 tool calls" in result.answer
    assert sum(r["path"].endswith(":generateContent") for r in mock.requests) <= 4
