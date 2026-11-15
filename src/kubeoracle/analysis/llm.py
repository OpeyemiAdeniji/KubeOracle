"""LLM providers. KubeOracle works without any; an LLM only explains and ranks evidence the checks found.

Providers:
  anthropic   Claude through the official Anthropic SDK (pip install 'kubeoracle[anthropic]')
  openai      any OpenAI-compatible endpoint: OpenAI, OpenRouter, Ollama, vLLM, LiteLLM, Azure gateways
  none        rules only
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

import httpx

from kubeoracle.config import Settings

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
            raise LLMError("Install the Anthropic SDK: pip install 'kubeoracle[anthropic]'") from exc
        self._anthropic = anthropic
        kwargs: dict[str, Any] = {"timeout": 180.0}
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
        except a.BadRequestError:
            response = self.client.messages.create(**request)
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


class OpenAICompatibleProvider(Provider):
    name = "openai"

    def __init__(self, settings: Settings):
        self.base_url = (settings.resolved_base_url() or "https://api.openai.com/v1").rstrip("/")
        self.model = settings.resolved_model()
        self.api_key = settings.resolved_api_key()
        self.provider = settings.resolved_provider()
        if self.provider != "ollama" and not self.api_key and "localhost" not in self.base_url:
            raise LLMError(f"No API key for {self.provider}; set {self.provider.upper()}_API_KEY or KUBEORACLE_LLM_API_KEY.")

    def complete_json(self, system: str, user: str, schema: dict[str, Any]) -> dict[str, Any]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        if self.provider == "openrouter":
            headers["X-Title"] = "KubeOracle"
        body: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system + "\n\nReply with one JSON object matching this schema:\n"
                 + json.dumps(schema)},
                {"role": "user", "content": user},
            ],
            "response_format": {"type": "json_object"},
        }
        with httpx.Client(timeout=180.0) as http:
            for attempt in range(3):
                try:
                    r = http.post(f"{self.base_url}/chat/completions", headers=headers, json=body)
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
    if name in ("openai", "openrouter", "ollama"):
        return OpenAICompatibleProvider(settings)
    raise LLMError(f"Unknown LLM provider {name!r}; use anthropic, openai, openrouter, ollama or none.")
