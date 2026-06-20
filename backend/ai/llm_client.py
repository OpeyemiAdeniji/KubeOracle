"""
Async HTTPX client for OpenRouter.

Uses asyncio.run() to drive the async call from synchronous investigation
service code. FastAPI runs sync `def` endpoints in a thread pool — those
threads have no event loop, so asyncio.run() is safe there.
"""

import asyncio
import json
import re
import httpx
from loguru import logger

from core.config import settings

_BASE_URL = "https://openrouter.ai/api/v1/chat/completions"
_TIMEOUT = 60.0
_MAX_RETRIES = 3
_RETRY_ON = {429, 500, 502, 503, 504}


async def _async_call(messages: list[dict], model: str) -> str:
    headers = {
        "Authorization": f"Bearer {settings.openrouter_api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://ai-kubernetes-agent",
        "X-Title": "AI Kubernetes Agent",
    }
    payload = {
        "model": model,
        "messages": messages,
        "temperature": 0.1,
        "max_tokens": 2048,
    }

    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        for attempt in range(1, _MAX_RETRIES + 1):
            try:
                logger.info(f"LLM request — model={model}  attempt={attempt}/{_MAX_RETRIES}")
                response = await client.post(_BASE_URL, headers=headers, json=payload)

                if response.status_code in _RETRY_ON and attempt < _MAX_RETRIES:
                    logger.warning(f"HTTP {response.status_code} — retrying")
                    await asyncio.sleep(attempt * 2)
                    continue

                response.raise_for_status()
                data = response.json()
                content: str = data["choices"][0]["message"]["content"]
                usage = data.get("usage", {})
                logger.info(
                    f"LLM response received  "
                    f"input_tokens={usage.get('prompt_tokens', '?')}  "
                    f"output_tokens={usage.get('completion_tokens', '?')}"
                )
                return content

            except httpx.TimeoutException:
                logger.warning(f"LLM timeout on attempt {attempt}")
                if attempt == _MAX_RETRIES:
                    raise RuntimeError("LLM request timed out after all retries")

            except httpx.HTTPStatusError as exc:
                logger.error(f"LLM API error: {exc.response.status_code} — {exc.response.text[:200]}")
                raise

    raise RuntimeError("LLM call exhausted all retries without a response")


def call_llm(messages: list[dict]) -> str:
    """Synchronous entry point. Blocks until the LLM responds."""
    if not settings.openrouter_api_key:
        raise ValueError("OPENROUTER_API_KEY is not set — add it to backend/.env")

    model = settings.openrouter_model or "openai/gpt-4o-mini"
    return asyncio.run(_async_call(messages, model))


def parse_llm_json(content: str) -> dict:
    """
    Extract a JSON object from the LLM response.

    Handles cases where the model wraps JSON in markdown code fences
    or adds prose before/after the object.
    """
    # Strip markdown code fences
    content = re.sub(r"```(?:json)?\s*", "", content).strip()

    # Find the outermost JSON object
    start = content.find("{")
    end = content.rfind("}") + 1
    if start == -1 or end == 0:
        raise ValueError(f"No JSON object in LLM response. Got: {content[:300]}")

    return json.loads(content[start:end])
