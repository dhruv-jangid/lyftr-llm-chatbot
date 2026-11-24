"""Backend tests. LLM calls go to a fake local server (see fake_llm.py),
so no real API keys or credits are needed."""
import sqlite3
import time
import uuid

import fake_llm
from conftest import TEST_KEYS

from app.config import settings


def new_session() -> str:
    return "s-" + uuid.uuid4().hex[:8]


def unique(text: str) -> str:
    # The LiteLLM cache is shared by all tests, so make messages unique
    # unless a test wants a cache hit on purpose.
    return f"{text} [{uuid.uuid4().hex[:6]}]"


def chat(client, session_id, message, headers=None, **extra):
    return client.post("/chat", json={"session_id": session_id, "message": message, **extra}, headers=headers or {})


def wait_for_cache_write():
    # LiteLLM writes to the cache in a background task right after responding.
    time.sleep(0.3)


# 1. health
def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok", "keys_configured": 2, "cache_enabled": True}


# 2 + 14. successful chat and token metadata
def test_successful_chat_returns_contract_fields(client):
    sid = new_session()
    r = chat(client, sid, unique("What is the capital of France?"))
    assert r.status_code == 200
    body = r.json()
    assert body["session_id"] == sid
    assert body["reply"].startswith("echo: What is the capital of France?")
    meta = body["metadata"]
    assert meta["resolution_source"] == "LIVE_INFERENCE"
    assert meta["model"] == "openai/fake-model"
    assert isinstance(meta["latency_ms"], int) and meta["latency_ms"] > 0
    assert meta["key_id"] == "key_1"
    assert meta["fallback_attempts"] == 0
    # tokens must equal what the provider reported for THIS request
    sent = fake_llm.CALLS[-1]["messages"]
    assert meta["input_tokens"] == fake_llm.count_tokens("".join(m["content"] for m in sent))
    assert meta["output_tokens"] == fake_llm.count_tokens(body["reply"])
    # the real key never appears in the response
    assert all(k not in r.text for k in TEST_KEYS)


def test_tokens_are_per_request_not_running_total(client):
    sid = new_session()
    first = chat(client, sid, unique("hello there")).json()
    second = chat(client, sid, unique("hello again")).json()
    # input of turn 2 = system + history + new message (as the provider counted it)
    sent = fake_llm.CALLS[-1]["messages"]
    assert second["metadata"]["input_tokens"] == fake_llm.count_tokens("".join(m["content"] for m in sent))
    # output tokens describe only this reply, never a conversation total
    assert second["metadata"]["output_tokens"] == fake_llm.count_tokens(second["reply"])
    assert second["metadata"]["output_tokens"] < first["metadata"]["output_tokens"] + second["metadata"]["output_tokens"]
    # two equal-length first messages in new sessions -> about the same input tokens
    a = chat(client, new_session(), unique("aaaa bbbb")).json()["metadata"]["input_tokens"]
    b = chat(client, new_session(), unique("cccc dddd")).json()["metadata"]["input_tokens"]
    assert abs(a - b) <= 1


# 3 + 4. validation
def test_empty_and_whitespace_message_is_422(client):
    for msg in ["", "    "]:
        r = chat(client, new_session(), msg)
        assert r.status_code == 422
        assert "detail" in r.json()


def test_message_over_4000_chars_is_422_but_4000_ok(client):
    assert chat(client, new_session(), "x" * 4001).status_code == 422
    assert chat(client, new_session(), "  " + "y" * 3994 + uuid.uuid4().hex[:6] + "  ").status_code == 200


def test_missing_or_invalid_fields_are_422(client):
    assert client.post("/chat", json={"message": "hi"}).status_code == 422
    assert client.post("/chat", json={"session_id": "s1"}).status_code == 422
    assert client.post("/chat", json={"session_id": "", "message": "hi"}).status_code == 422
    assert client.post("/chat", json={"session_id": "x" * 129, "message": "hi"}).status_code == 422
    assert client.post("/chat", json={"session_id": 123, "message": "hi"}).status_code == 422
    r = client.post("/chat", content=b"{not json", headers={"Content-Type": "application/json"})
    assert r.status_code == 422 and "detail" in r.json()


# 5. unknown session handling
def test_unknown_session_starts_new_and_history_404(client):
    r = client.get(f"/sessions/{new_session()}/messages")
    assert r.status_code == 404
    assert r.json() == {"detail": "session not found"}
    sid = new_session()  # unknown id on /chat simply starts a new session
    assert chat(client, sid, unique("hi")).status_code == 200
    assert client.get(f"/sessions/{sid}/messages").status_code == 200


# 6. multi-turn history
def test_multi_turn_history_is_sent_to_llm_and_returned(client):
    sid = new_session()
    m1, m2 = unique("My name is Dhruv."), unique("What is my name?")
    chat(client, sid, m1)
    r = chat(client, sid, m2)
    assert "user turns seen: 2" in r.json()["reply"]
    sent = fake_llm.CALLS[-1]["messages"]
    assert [m["role"] for m in sent] == ["system", "user", "assistant", "user"]
    assert sent[1]["content"] == m1 and sent[3]["content"] == m2

    hist = client.get(f"/sessions/{sid}/messages").json()
    assert hist["session_id"] == sid
    roles = [m["role"] for m in hist["messages"]]
    assert roles == ["user", "assistant", "user", "assistant"]  # oldest first, no system
    assert hist["messages"][0]["content"] == m1
    assert "metadata" not in hist["messages"][0]
    meta = hist["messages"][1]["metadata"]
    assert meta["resolution_source"] == "LIVE_INFERENCE"
    assert {"input_tokens", "output_tokens"} <= meta.keys()


def test_system_prompt_is_used_but_never_exposed(client):
    sid = new_session()
    chat(client, sid, unique("hi"), system_prompt="You are a pirate.")
    assert fake_llm.CALLS[-1]["messages"][0] == {"role": "system", "content": "You are a pirate."}
    chat(client, sid, unique("again"))  # remembered for the session
    assert fake_llm.CALLS[-1]["messages"][0]["content"] == "You are a pirate."
    text = client.get(f"/sessions/{sid}/messages").text
    assert "pirate" not in text


# 7. separate sessions
def test_sessions_are_separate(client):
    a, b = new_session(), new_session()
    chat(client, a, unique("secret A"))
    r = chat(client, b, unique("question B"))
    assert "user turns seen: 1" in r.json()["reply"]
    assert len(client.get(f"/sessions/{a}/messages").json()["messages"]) == 2
    assert len(client.get(f"/sessions/{b}/messages").json()["messages"]) == 2


# 8. cache hit
def test_same_first_message_in_new_session_is_cache_hit(client):
    msg = unique("Tell me a fun fact")
    live = chat(client, new_session(), msg).json()
    wait_for_cache_write()
    calls_before = len(fake_llm.CALLS)
    cached = chat(client, new_session(), msg).json()
    assert len(fake_llm.CALLS) == calls_before  # provider was NOT called
    assert live["metadata"]["resolution_source"] == "LIVE_INFERENCE"
    assert cached["metadata"]["resolution_source"] == "LLM_CACHE"
    assert cached["reply"] == live["reply"]
    assert cached["metadata"]["key_id"] is None
    assert cached["metadata"]["fallback_attempts"] == 0
    assert cached["metadata"]["input_tokens"] == live["metadata"]["input_tokens"]
    assert cached["metadata"]["output_tokens"] == live["metadata"]["output_tokens"]
    assert cached["metadata"]["latency_ms"] < live["metadata"]["latency_ms"]


# 9. cache misses
def test_different_message_or_system_prompt_is_not_cached(client):
    msg = unique("Cache me")
    chat(client, new_session(), msg)
    wait_for_cache_write()
    other = chat(client, new_session(), unique("Something else")).json()
    assert other["metadata"]["resolution_source"] == "LIVE_INFERENCE"
    diff_prompt = chat(client, new_session(), msg, system_prompt="Be formal.").json()
    assert diff_prompt["metadata"]["resolution_source"] == "LIVE_INFERENCE"


def test_same_message_with_different_history_is_not_cached(client):
    msg = unique("And then?")
    chat(client, new_session(), msg)
    wait_for_cache_write()
    sid = new_session()
    chat(client, sid, unique("first"))
    r = chat(client, sid, msg).json()
    assert r["metadata"]["resolution_source"] == "LIVE_INFERENCE"


# 10. real failover (provider really returns 429 / 5xx for key_1)
def test_real_provider_429_fails_over_to_key_2(client):
    for status in (429, 500, 503):
        fake_llm.FAILING_KEYS[TEST_KEYS[0]] = status
        r = chat(client, new_session(), unique(f"real failover {status}"))
        assert r.status_code == 200, r.text
        meta = r.json()["metadata"]
        assert meta["key_id"] == "key_2"
        assert meta["fallback_attempts"] == 1
        assert meta["resolution_source"] == "LIVE_INFERENCE"


# 11. all keys failing
def test_all_real_keys_failing_returns_503(client):
    fake_llm.FAILING_KEYS.update({TEST_KEYS[0]: 429, TEST_KEYS[1]: 500})
    sid = new_session()
    r = chat(client, sid, unique("nobody home"))
    assert r.status_code == 503
    assert r.json() == {"detail": "all providers unavailable"}


# 12. X-Simulate-Failure header
def test_simulate_key_1_429_and_500_answer_with_key_2(client):
    for header in ("key_1=429", "key_1=500", "key_1=502", "key_1=503"):
        r = chat(client, new_session(), unique("simulated"), headers={"X-Simulate-Failure": header})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["reply"].startswith("echo: simulated")  # a real answer
        assert body["metadata"]["key_id"] == "key_2"
        assert body["metadata"]["fallback_attempts"] == 1
        assert fake_llm.CALLS[-1]["api_key"] == TEST_KEYS[1]


def test_simulate_all_keys_fail_returns_503(client):
    for header in ("*=429", "key_1=429,key_2=500"):
        sid = new_session()
        r = chat(client, sid, unique("doomed"), headers={"X-Simulate-Failure": header})
        assert r.status_code == 503
        assert r.json() == {"detail": "all providers unavailable"}
        assert client.get(f"/sessions/{sid}/messages").status_code == 404


def test_simulation_bypasses_cache_read_so_failover_is_visible(client):
    msg = unique("popular question")
    chat(client, new_session(), msg)
    wait_for_cache_write()
    r = chat(client, new_session(), msg, headers={"X-Simulate-Failure": "key_1=429"}).json()
    assert r["metadata"]["resolution_source"] == "LIVE_INFERENCE"
    assert r["metadata"]["key_id"] == "key_2"
    r = chat(client, new_session(), msg, headers={"X-Simulate-Failure": "*=429"})
    assert r.status_code == 503


def test_simulation_affects_only_that_request(client):
    chat(client, new_session(), unique("x"), headers={"X-Simulate-Failure": "key_1=429"})
    r = chat(client, new_session(), unique("normal again")).json()
    assert r["metadata"]["key_id"] == "key_1"
    assert r["metadata"]["fallback_attempts"] == 0


def test_invalid_simulation_header_is_422(client):
    r = chat(client, new_session(), unique("x"), headers={"X-Simulate-Failure": "key_1=418"})
    assert r.status_code == 422


def test_simulation_header_ignored_when_disabled(client, monkeypatch):
    monkeypatch.setattr(settings, "enable_failure_simulation", False)
    r = chat(client, new_session(), unique("x"), headers={"X-Simulate-Failure": "*=429"})
    assert r.status_code == 200
    assert r.json()["metadata"]["key_id"] == "key_1"


# 13. failed requests are not stored
def test_failed_requests_not_added_to_existing_history(client):
    sid = new_session()
    chat(client, sid, unique("first ok"))
    assert chat(client, sid, "").status_code == 422
    assert chat(client, sid, unique("fails"), headers={"X-Simulate-Failure": "*=503"}).status_code == 503
    msgs = client.get(f"/sessions/{sid}/messages").json()["messages"]
    assert len(msgs) == 2
    # and the next LLM call does not see the failed message
    chat(client, sid, unique("third"))
    assert len([m for m in fake_llm.CALLS[-1]["messages"] if m["role"] == "user"]) == 2


# 15. SQLite persistence
def test_data_is_persisted_in_sqlite_file(client):
    sid = new_session()
    msg = unique("persist me")
    chat(client, sid, msg)
    conn = sqlite3.connect(settings.database_path)
    rows = conn.execute(
        "SELECT role, content, metadata, created_at FROM messages WHERE session_id=? ORDER BY id", (sid,)
    ).fetchall()
    conn.close()
    assert [r[0] for r in rows] == ["user", "assistant"]
    assert rows[0][1] == msg and rows[0][2] is None
    assert '"resolution_source"' in rows[1][2]
    assert "T" in rows[0][3]  # ISO-8601 timestamp


def test_unknown_route_and_errors_are_json(client):
    r = client.get("/does-not-exist")
    assert r.status_code == 404 and r.headers["content-type"].startswith("application/json")
