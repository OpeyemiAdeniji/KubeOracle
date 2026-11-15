"""LLM providers. KubeSift works without any; an LLM only explains and ranks evidence the checks found.

Providers:
  anthropic   Claude through the official Anthropic SDK (pip install 'kubesift[anthropic]')
  openai      any OpenAI-compatible endpoint: OpenAI, OpenRouter, vLLM, LiteLLM, Azure gateways
  openrouter  OpenRouter (OpenAI-compatible)
  ollama      local models through Ollama's native API
  none        rules only
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

import httpx

from kubesift.config import Settings

log = logging.getLogger(__name__)


class LLMError(Exception):
    pass


class Provider:
    name = "none"

    def complete_json(self, system: str, user: str, schema: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError


class AnthropicProvider(Provider):
    name = "anthropic"

    def __init__(self, settings: Settings):
        try:
            import anthropic
        except ImportError as exc:
            raise LLMError("Install the Anthropic SDK: pip install 'kubesift[anthropic]'") from exc
        self._anthropic = anthropic
        self.timeout = settings.resolved_llm_timeout()
        kwargs: dict[str, Any] = {"timeout": self.timeout}
        if settings.llm_api_key:
            kwargs["api_key"] = settings.llm_api_key
        if settings.llm_base_url:
            kwargs["base_url"] = settings.llm_base_url
        self.client = anthropic.Anthropic(**kwargs)
        self.model = settings.resolved_model()
        self.effort = settings.llm_effort

    def complete_json(self, system: str, user: str, schema: dict[str, Any]) -> dict[str, Any]:
        a = self._anthropic
        request = {
            "model": self.model,
            "max_tokens": 16000,
            "system": system,
            "messages": [{"role": "user", "content": user}],
            "output_config": {"effort": self.effort, "format": {"type": "json_schema", "schema": schema}},
        }
        try:
            # Server-side fallback re-runs a declined request on another model inside the same call.
            response = self.client.beta.messages.create(
                **request, betas=["server-side-fallback-2026-07-01"], fallbacks="default")
        except (a.BadRequestError, TypeError):
            # Older SDKs (TypeError) or endpoints (400) without server-side fallback: send a plain request.
            try:
                response = self.client.messages.create(**request)
            except a.APIStatusError as exc:
                raise LLMError(f"Anthropic API error {exc.status_code}: {exc.message}") from exc
            except a.APITimeoutError as exc:
                raise LLMError(_timeout_message(self.model, self.timeout)) from exc
            except a.APIConnectionError as exc:
                raise LLMError(f"Cannot reach the Anthropic API: {exc}") from exc
        except a.APITimeoutError as exc:
            raise LLMError(_timeout_message(self.model, self.timeout)) from exc
        except a.AuthenticationError as exc:
            raise LLMError("Anthropic rejected the API key (set ANTHROPIC_API_KEY).") from exc
        except a.RateLimitError as exc:
            raise LLMError("Anthropic rate limit reached; try again shortly.") from exc
        except a.APIStatusError as exc:
            raise LLMError(f"Anthropic API error {exc.status_code}: {exc.message}") from exc
        except a.APIConnectionError as exc:
            raise LLMError(f"Cannot reach the Anthropic API: {exc}") from exc
        if response.stop_reason == "refusal":
            raise LLMError("The model declined this request.")
        text = "".join(b.text for b in response.content if getattr(b, "type", "") == "text")
        return parse_json(text)


def _timeout_message(model: str, seconds: float) -> str:
    return (f"{model} did not answer within {seconds:.0f}s. Local models on a CPU can take many minutes: "
            "raise llm_timeout (KUBESIFT_LLM_TIMEOUT), use a smaller or hosted model, or run with --no-llm.")


def _with_schema(system: str, schema: dict[str, Any]) -> str:
    return system + "\n\nReply with one JSON object matching this schema:\n" + json.dumps(schema)


class OpenAICompatibleProvider(Provider):
    name = "openai"

    def __init__(self, settings: Settings):
        self.base_url = (settings.resolved_base_url() or "https://api.openai.com/v1").rstrip("/")
        self.model = settings.resolved_model()
        self.api_key = settings.resolved_api_key()
        self.provider = settings.resolved_provider()
        self.timeout = settings.resolved_llm_timeout()
        if self.provider != "ollama" and not self.api_key and "localhost" not in self.base_url:
            raise LLMError(f"No API key for {self.provider}; set {self.provider.upper()}_API_KEY or KUBESIFT_LLM_API_KEY.")

    def complete_json(self, system: str, user: str, schema: dict[str, Any]) -> dict[str, Any]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        if self.provider == "openrouter":
            headers["X-Title"] = "KubeSift"
        body: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": _with_schema(system, schema)},
                {"role": "user", "content": user},
            ],
            "response_format": {"type": "json_object"},
        }
        with httpx.Client(timeout=httpx.Timeout(self.timeout, connect=15.0)) as http:
            for attempt in range(3):
                try:
                    r = http.post(f"{self.base_url}/chat/completions", headers=headers, json=body)
                except httpx.TimeoutException as exc:
                    if isinstance(exc, httpx.ConnectTimeout) and attempt < 2:
                        continue
                    # a slow answer is not retried: the next attempt would redo the same slow work
                    raise LLMError(_timeout_message(self.model, self.timeout)) from exc
                except httpx.HTTPError as exc:
                    if attempt == 2:
                        raise LLMError(f"Cannot reach {self.base_url}: {exc}") from exc
                    continue
                if r.status_code == 400 and "response_format" in body:
                    body.pop("response_format")  # some servers don't support JSON mode
                    continue
                if r.status_code in (429, 500, 502, 503, 504) and attempt < 2:
                    continue
                if r.status_code >= 400:
                    raise LLMError(f"{self.provider} returned HTTP {r.status_code}: {r.text[:300]}")
                data = r.json()
                return parse_json(data["choices"][0]["message"]["content"] or "")
        raise LLMError("LLM request failed after retries")


class OllamaProvider(Provider):
    """Ollama's native API: turns off hidden reasoning, sizes the context window to the prompt (Ollama's
    default window is small and silently drops the start of longer prompts) and constrains output to the schema."""

    name = "ollama"
    MAX_OUTPUT = 4096

    def __init__(self, settings: Settings):
        base = (settings.resolved_base_url() or "http://localhost:11434").rstrip("/")
        self.base_url = base[:-3] if base.endswith("/v1") else base
        self.model = settings.resolved_model()
        self.timeout = settings.resolved_llm_timeout()
        self.context = settings.llm_context
        self.api_key = settings.resolved_api_key()

    def status(self) -> tuple[bool, str]:
        """Is the server up and the model pulled? Used by `kubesift doctor`."""
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        try:
            r = httpx.get(f"{self.base_url}/api/tags", headers=headers, timeout=5.0)
            r.raise_for_status()
            names = {m.get("name") for m in r.json().get("models") or []}
        except (httpx.HTTPError, ValueError) as exc:
            return False, f"cannot reach Ollama at {self.base_url} ({exc.__class__.__name__}); is it running? (ollama serve)"
        wanted = self.model if ":" in self.model else f"{self.model}:latest"
        if wanted not in names and self.model not in names:
            return False, f"Ollama is running but has no model {self.model!r}; run: ollama pull {self.model}"
        return True, f"Ollama at {self.base_url} has {self.model}"

    def context_size(self, text_chars: int) -> int:
        if self.context:
            return self.context
        needed = text_chars // 3 + self.MAX_OUTPUT  # ~3 characters per token for JSON-heavy text
        return min(max(8192, -(-needed // 2048) * 2048), 32768)

    def complete_json(self, system: str, user: str, schema: dict[str, Any]) -> dict[str, Any]:
        system = _with_schema(system, schema)
        body: dict[str, Any] = {
            "model": self.model,
            "stream": False,
            "think": False,
            "format": schema,
            "options": {"num_ctx": self.context_size(len(system) + len(user)), "num_predict": self.MAX_OUTPUT,
                        "temperature": 0.2},
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        }
        url = f"{self.base_url}/api/chat"
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        with httpx.Client(timeout=httpx.Timeout(self.timeout, connect=10.0)) as http:
            for _ in range(3):
                try:
                    r = http.post(url, json=body, headers=headers)
                except httpx.ConnectError as exc:
                    raise LLMError(f"Cannot reach Ollama at {self.base_url}; is it running? (ollama serve)") from exc
                except httpx.TimeoutException as exc:
                    raise LLMError(_timeout_message(self.model, self.timeout)) from exc
                except httpx.HTTPError as exc:
                    raise LLMError(f"Ollama request failed: {exc}") from exc
                if r.status_code == 404 and "not found" in r.text.lower():
                    raise LLMError(f"Ollama has no model {self.model!r}; run: ollama pull {self.model}")
                if r.status_code == 400 and "think" in body and "think" in r.text.lower():
                    body.pop("think")  # older Ollama or a model without a thinking switch
                    continue
                if r.status_code == 400 and isinstance(body.get("format"), dict):
                    body["format"] = "json"  # older Ollama without schema-constrained output
                    continue
                if r.status_code >= 400:
                    raise LLMError(f"Ollama returned HTTP {r.status_code}: {r.text[:300]}")
                data = r.json()
                if data.get("done_reason") == "length":
                    raise LLMError("The model ran out of output tokens before finishing its answer.")
                return parse_json((data.get("message") or {}).get("content") or "")
        raise LLMError("Ollama rejected the request.")


def parse_json(text: str) -> dict[str, Any]:
    text = re.sub(r"```(?:json)?", "", text).strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise LLMError("The model did not return JSON.")
    try:
        return json.loads(text[start:end + 1])
    except json.JSONDecodeError as exc:
        raise LLMError(f"The model returned invalid JSON: {exc}") from exc


def get_provider(settings: Settings) -> Provider | None:
    name = settings.resolved_provider()
    if name == "none":
        return None
    if name == "anthropic":
        return AnthropicProvider(settings)
    if name == "ollama":
        return OllamaProvider(settings)
    if name in ("openai", "openrouter"):
        return OpenAICompatibleProvider(settings)
    raise LLMError(f"Unknown LLM provider {name!r}; use anthropic, openai, openrouter, ollama or none.")
