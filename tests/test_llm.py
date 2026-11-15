"""LLM providers against a local stand-in HTTP server: request shape and response parsing."""

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from kubesift.analysis.engine import DIAGNOSIS_SCHEMA
from kubesift.analysis.llm import AnthropicProvider, LLMError, OpenAICompatibleProvider, parse_json
from kubesift.config import Settings

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
