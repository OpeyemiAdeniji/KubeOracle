"""LLM providers against a local stand-in HTTP server: request shape and response parsing."""

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from kubesift.analysis.engine import DIAGNOSIS_SCHEMA
from kubesift.analysis.llm import (
    AnthropicProvider,
    LLMError,
    OllamaProvider,
    OpenAICompatibleProvider,
    get_provider,
    parse_json,
)
from kubesift.config import Settings, load_settings

ANSWER = {"summary": "s", "root_causes": [], "next_checks": []}


@pytest.fixture
def llm_server():
    requests: list[dict] = []
    responses: list[tuple[int, dict]] = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append({"path": self.path, "headers": dict(self.headers), "body": body})
            code, payload = responses.pop(0) if responses else (200, {})
            if "_sleep" in payload:
                time.sleep(payload.pop("_sleep"))  # a model that is slow to answer
            data = json.dumps(payload).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}", requests, responses
    server.shutdown()


def anthropic_message(text, stop="end_turn"):
    return {"id": "msg_1", "type": "message", "role": "assistant", "model": "claude-opus-5-5",
            "content": [{"type": "text", "text": text}], "stop_reason": stop, "stop_sequence": None,
            "usage": {"input_tokens": 10, "output_tokens": 5}}


def test_anthropic_request_and_parse(llm_server):
    pytest.importorskip("anthropic")
    url, requests, responses = llm_server
    responses.append((200, anthropic_message(json.dumps(ANSWER))))
    p = AnthropicProvider(Settings(llm_provider="anthropic", llm_api_key="k", llm_base_url=url))
    assert p.complete_json("sys", "user", DIAGNOSIS_SCHEMA) == ANSWER
    body = requests[0]["body"]
    assert requests[0]["path"].startswith("/v1/messages")
    assert body["model"] == "claude-opus-5-5"
    assert body["output_config"]["format"]["type"] == "json_schema"
    assert body["fallbacks"] == "default"
    assert "server-side-fallback-2026-07-01" in requests[0]["headers"].get("anthropic-beta", "")


def test_anthropic_retries_without_beta_on_400(llm_server):
    pytest.importorskip("anthropic")
    url, requests, responses = llm_server
    responses += [(400, {"type": "error", "error": {"type": "invalid_request_error", "message": "unsupported"}}),
                  (200, anthropic_message(json.dumps(ANSWER)))]
    p = AnthropicProvider(Settings(llm_provider="anthropic", llm_api_key="k", llm_base_url=url))
    assert p.complete_json("sys", "user", DIAGNOSIS_SCHEMA) == ANSWER
    assert "fallbacks" not in requests[-1]["body"]


def test_anthropic_refusal(llm_server):
    pytest.importorskip("anthropic")
    url, _, responses = llm_server
    responses.append((200, anthropic_message("", stop="refusal")))
    p = AnthropicProvider(Settings(llm_provider="anthropic", llm_api_key="k", llm_base_url=url))
    with pytest.raises(LLMError):
        p.complete_json("sys", "user", DIAGNOSIS_SCHEMA)


def test_openai_compatible(llm_server):
    url, requests, responses = llm_server
    responses += [(400, {"error": "no json mode"}),
                  (200, {"choices": [{"message": {"content": "```json\n" + json.dumps(ANSWER) + "\n```"}}]})]
    p = OpenAICompatibleProvider(Settings(llm_provider="openrouter", llm_api_key="k", llm_base_url=url, llm_model="m"))
    assert p.complete_json("sys", "user", DIAGNOSIS_SCHEMA) == ANSWER
    assert requests[0]["headers"]["Authorization"] == "Bearer k"
    assert "response_format" in requests[0]["body"] and "response_format" not in requests[1]["body"]


def test_parse_json_errors():
    with pytest.raises(LLMError):
        parse_json("no json here")
    assert parse_json('Sure! {"a": 1} hope that helps') == {"a": 1}


def test_provider_auto(monkeypatch):
    for k in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "OPENROUTER_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    assert Settings().resolved_provider() == "none"
    monkeypatch.setenv("OPENROUTER_API_KEY", "x")
    assert Settings().resolved_provider() == "openrouter"
    assert Settings().resolved_model() == "anthropic/claude-opus-5-5"


def test_llm_timeout_defaults_and_override(monkeypatch):
    assert Settings(llm_provider="ollama").resolved_llm_timeout() == 900
    assert Settings(llm_provider="anthropic").resolved_llm_timeout() == 180
    assert Settings(llm_provider="ollama", llm_timeout=30).resolved_llm_timeout() == 30
    monkeypatch.setenv("KUBESIFT_LLM_TIMEOUT", "1200")
    assert load_settings(llm_provider="openai").resolved_llm_timeout() == 1200


def test_slow_answer_is_not_retried(llm_server):
    url, requests, responses = llm_server
    responses.append((200, {"_sleep": 2.5}))
    p = OpenAICompatibleProvider(Settings(llm_provider="openai", llm_api_key="k", llm_base_url=url, llm_timeout=1))
    with pytest.raises(LLMError, match="did not answer within 1s"):
        p.complete_json("sys", "user", DIAGNOSIS_SCHEMA)
    assert len(requests) == 1


def test_ollama_uses_native_api(llm_server):
    url, requests, responses = llm_server
    responses.append((200, {"message": {"role": "assistant", "content": json.dumps(ANSWER)}, "done": True,
                            "done_reason": "stop"}))
    p = get_provider(Settings(llm_provider="ollama", llm_base_url=url + "/v1", llm_model="qwen3.5:4b"))
    assert isinstance(p, OllamaProvider) and p.base_url == url
    assert p.complete_json("sys", "user", DIAGNOSIS_SCHEMA) == ANSWER
    req = requests[0]
    assert req["path"] == "/api/chat"
    body = req["body"]
    assert body["think"] is False and body["stream"] is False and body["format"] == DIAGNOSIS_SCHEMA
    assert body["options"]["num_ctx"] >= 8192 and body["options"]["num_predict"] == OllamaProvider.MAX_OUTPUT


def test_ollama_context_is_sized_to_the_prompt():
    p = OllamaProvider(Settings(llm_provider="ollama"))
    assert p.context_size(1000) == 8192
    assert p.context_size(60000) == 24576  # 20000 prompt tokens + 4096 output, rounded up
    assert p.context_size(10_000_000) == 32768
    assert OllamaProvider(Settings(llm_provider="ollama", llm_context=4096)).context_size(60000) == 4096


def test_ollama_falls_back_for_older_servers(llm_server):
    url, requests, responses = llm_server
    responses += [(400, {"error": 'think value "false" is not supported for this model'}),
                  (400, {"error": "invalid format"}),
                  (200, {"message": {"content": json.dumps(ANSWER)}, "done": True})]
    p = OllamaProvider(Settings(llm_provider="ollama", llm_base_url=url))
    assert p.complete_json("sys", "user", DIAGNOSIS_SCHEMA) == ANSWER
    assert "think" not in requests[1]["body"] and requests[2]["body"]["format"] == "json"


def test_ollama_explains_common_failures(llm_server):
    url, _, responses = llm_server
    responses.append((404, {"error": 'model "qwen3.5:4b" not found, try pulling it first'}))
    p = OllamaProvider(Settings(llm_provider="ollama", llm_base_url=url, llm_model="qwen3.5:4b"))
    with pytest.raises(LLMError, match="ollama pull qwen3.5:4b"):
        p.complete_json("sys", "user", DIAGNOSIS_SCHEMA)
    responses.append((200, {"message": {"content": '{"summary": "cut'}, "done": True, "done_reason": "length"}))
    with pytest.raises(LLMError, match="ran out of output tokens"):
        p.complete_json("sys", "user", DIAGNOSIS_SCHEMA)
    down = OllamaProvider(Settings(llm_provider="ollama", llm_base_url="http://127.0.0.1:9"))
    with pytest.raises(LLMError, match="is it running"):
        down.complete_json("sys", "user", DIAGNOSIS_SCHEMA)


def test_ollama_status_and_api_key(llm_server, monkeypatch):
    import httpx

    url, requests, responses = llm_server
    tags = {"models": [{"name": "qwen3.5:4b"}, {"name": "llama3.1:latest"}]}
    monkeypatch.setattr(httpx, "get", lambda *a, **k: httpx.Response(200, json=tags, request=httpx.Request("GET", a[0])))
    assert OllamaProvider(Settings(llm_provider="ollama", llm_model="qwen3.5:4b")).status()[0]
    assert OllamaProvider(Settings(llm_provider="ollama", llm_model="llama3.1")).status()[0]
    ok, detail = OllamaProvider(Settings(llm_provider="ollama", llm_model="mistral")).status()
    assert not ok and "ollama pull mistral" in detail
    monkeypatch.setenv("OLLAMA_API_KEY", "ok-key")
    responses.append((200, {"message": {"content": json.dumps(ANSWER)}, "done": True}))
    OllamaProvider(Settings(llm_provider="ollama", llm_base_url=url)).complete_json("s", "u", DIAGNOSIS_SCHEMA)
    assert requests[-1]["headers"]["Authorization"] == "Bearer ok-key"
