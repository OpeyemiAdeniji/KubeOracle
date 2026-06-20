"""
Root cause analyzer — orchestrates the full AI reasoning pipeline:

  investigation payload
      ↓ confidence_engine  (rule-based baseline score)
      ↓ prompt_builder     (structured SRE prompt)
      ↓ llm_client         (OpenRouter via async HTTPX)
      ↓ parse + validate
      → diagnosis dict
"""

from loguru import logger

from ai.confidence_engine import calculate_baseline_confidence
from ai.prompt_builder import SYSTEM_PROMPT, build_prompt
from ai.llm_client import call_llm, parse_llm_json

_VALID_SEVERITIES = {"critical", "high", "medium", "low"}

_FALLBACK_DIAGNOSIS = {
    "root_cause": "AI analysis unavailable",
    "explanation": (
        "The AI reasoning engine could not process the investigation data. "
        "Check OPENROUTER_API_KEY configuration and cluster connectivity."
    ),
    "fix": "Verify backend/.env contains a valid OPENROUTER_API_KEY.",
    "kubectl_commands": [],
    "prevention": "Ensure OPENROUTER_API_KEY is set and the OpenRouter API is reachable.",
    "contributing_factors": [],
    "severity": "low",
    "confidence": 0,
}


def analyze(investigation: dict) -> dict:
    """
    Run the full AI reasoning pipeline against the investigation payload.
    Returns a diagnosis dict. Never raises — falls back gracefully on errors.
    """
    logger.info("Running AI root cause analysis...")

    # ── Step 1: baseline confidence ──────────────────────────────────────────
    baseline_confidence, evidence_summary = calculate_baseline_confidence(investigation)
    logger.info(f"Baseline confidence: {baseline_confidence}/100")

    # ── Step 2: build prompt ──────────────────────────────────────────────────
    user_prompt = build_prompt(investigation, baseline_confidence, evidence_summary)

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]

    # ── Step 3: call LLM ──────────────────────────────────────────────────────
    try:
        raw_response = call_llm(messages)
    except Exception as exc:
        logger.error(f"LLM call failed: {exc}")
        return {**_FALLBACK_DIAGNOSIS, "error": str(exc)}

    # ── Step 4: parse JSON ────────────────────────────────────────────────────
    try:
        diagnosis = parse_llm_json(raw_response)
    except Exception as exc:
        logger.error(f"Failed to parse LLM response as JSON: {exc}")
        logger.debug(f"Raw LLM output: {raw_response[:500]}")
        return {**_FALLBACK_DIAGNOSIS, "error": f"JSON parse error: {exc}", "raw_response": raw_response[:500]}

    # ── Step 5: normalise & validate ─────────────────────────────────────────
    diagnosis.setdefault("root_cause", "Unknown")
    diagnosis.setdefault("explanation", "")
    diagnosis.setdefault("fix", "")
    diagnosis.setdefault("kubectl_commands", [])
    diagnosis.setdefault("prevention", "")
    diagnosis.setdefault("contributing_factors", [])
    diagnosis.setdefault("severity", "medium")
    diagnosis.setdefault("confidence", baseline_confidence)

    # Ensure severity is a known value
    if diagnosis["severity"] not in _VALID_SEVERITIES:
        diagnosis["severity"] = "medium"

    # Clamp confidence to 0-100
    try:
        diagnosis["confidence"] = max(0, min(100, int(diagnosis["confidence"])))
    except (TypeError, ValueError):
        diagnosis["confidence"] = baseline_confidence

    # Ensure kubectl_commands is a list of strings
    cmds = diagnosis.get("kubectl_commands", [])
    if not isinstance(cmds, list):
        diagnosis["kubectl_commands"] = [str(cmds)]
    else:
        diagnosis["kubectl_commands"] = [str(c).strip() for c in cmds if c]

    logger.info(
        f"Diagnosis complete — "
        f"severity={diagnosis['severity']}  "
        f"confidence={diagnosis['confidence']}"
    )
    return diagnosis
