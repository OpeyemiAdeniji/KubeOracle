# Prompt 03 — AI Reasoning Engine

## What This Prompt Was Trying to Achieve

Build the intelligence layer that takes raw investigation data from the Kubernetes inspectors and produces a structured, actionable diagnosis. The goal was a multi-stage reasoning pipeline: first score the evidence with deterministic rules (no LLM cost), then build a focused prompt, then call an LLM via OpenRouter, then parse and validate the structured JSON response. The engine needed to be fault-tolerant — a failed LLM call should never crash the API, only return a graceful fallback.

---

## Key Requirements Covered

- **`confidence_engine.py`** — rule-based pre-scoring that examines the investigation dict and produces a baseline confidence score (0–100) and an evidence summary string before the LLM is ever called. Evidence signals include: problematic pod count, restart counts, OOMKilled containers, critical events, unavailable deployments, NotReady nodes
- **`prompt_builder.py`** — builds a structured SRE-style system prompt and user prompt that includes the evidence summary, baseline confidence, and the full investigation payload serialised as JSON. The system prompt instructs the LLM to respond with a specific JSON schema
- **`llm_client.py`** — async HTTPX client that calls OpenRouter (`https://openrouter.ai/api/v1/chat/completions`) using the model from `settings.openrouter_model` (default: `anthropic/claude-sonnet-4-6`). Uses `asyncio.run()` inside the sync FastAPI endpoint (safe because thread pool workers have no running event loop)
- **`root_cause_analyzer.py`** — orchestrates the four-stage pipeline: confidence scoring → prompt building → LLM call → JSON parse/validate. Normalises and clamps all fields in the response; falls back to a static diagnosis dict on any error
- LLM response schema enforced post-parse:

| Field | Type | Description |
|-------|------|-------------|
| `root_cause` | string | One-line root cause label |
| `explanation` | string | Detailed SRE explanation |
| `fix` | string | Recommended remediation steps |
| `kubectl_commands` | string[] | Ready-to-run kubectl commands |
| `prevention` | string | Long-term prevention advice |
| `contributing_factors` | string[] | Secondary factors |
| `severity` | `critical` \| `high` \| `medium` \| `low` | Normalised severity |
| `confidence` | integer 0–100 | Clamped from LLM output |

---

## Tech Stack and Components Introduced

| Component | Detail |
|-----------|--------|
| LLM provider | OpenRouter (`https://openrouter.ai/api/v1`) |
| Default model | `anthropic/claude-sonnet-4-6` |
| HTTP client | `httpx` async client, called via `asyncio.run()` |
| Config | `OPENROUTER_API_KEY` in `backend/.env` — server-side only, never sent to frontend |
| Fallback | Static `_FALLBACK_DIAGNOSIS` dict returned on any exception |
| Severity validation | Rejects unknown values, defaults to `"medium"` |
| Confidence clamping | `max(0, min(100, int(value)))` |

---

## Expected Result

After running this prompt:

- `POST /investigate` returns a full diagnosis with root cause, explanation, fix steps, kubectl commands, severity, and confidence
- If OpenRouter is unreachable or returns malformed JSON, the endpoint still returns a structured response (the fallback diagnosis) with a descriptive error message — it never returns a 500
- The confidence score combines deterministic evidence signals with LLM self-assessment — the rule-based floor prevents the LLM from claiming high confidence with sparse data
- The system prompt is crafted as an SRE persona, producing concise, actionable output rather than verbose explanations
