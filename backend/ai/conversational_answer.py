"""
Conversational answer generator — produces a direct, SRE-style response to a
natural language Kubernetes question from targeted investigation data.
"""

from loguru import logger
from ai.llm_client import call_llm, parse_llm_json
from ai.prompt_builder import build_prompt
from ai.confidence_engine import calculate_baseline_confidence

SYSTEM_PROMPT = """You are a Senior SRE answering a Kubernetes question in a team chat.

Tone: direct, friendly, technical — like a Slack message, not a formal report.

Rules:
- Answer the SPECIFIC question asked, not a general cluster audit
- Lead with the direct answer in the first sentence
- Name exact resources from the data (pod names, namespaces, error messages)
- Keep prose under 120 words — then list kubectl commands separately
- If the cluster shows no issue related to the question, say so briefly and confidently
- kubectl commands must be copy-pasteable with real resource names from the evidence

You MUST respond with ONLY valid JSON. No markdown, no code fences, no text outside JSON."""

_USER_TEMPLATE = """The user asked: "{question}"

Cluster context: {context}
Question type: {question_type}
Target resource: {target_resource}
Target namespace: {target_namespace}
Keywords: {keywords}

Investigation findings:
{investigation_summary}

Respond with exactly this JSON (no other text):
{{
  "answer": "Direct conversational answer (prose only — no code blocks inside this field)",
  "kubectl_commands": ["kubectl command 1", "kubectl command 2"],
  "confidence": 85,
  "severity": "high"
}}

severity values: critical | high | medium | low
If nothing is wrong, use severity=low and confidence 90+."""

_VALID_SEVERITIES = frozenset({"critical", "high", "medium", "low"})


def generate_answer(
    question: str,
    intent: dict,
    investigation: dict,
    context: str = "",
) -> dict:
    """
    Generate a conversational answer to a natural language Kubernetes question.
    Returns dict: answer, kubectl_commands, confidence, severity.
    """
    logger.info(f"Generating conversational answer for: {question[:80]}")

    baseline, evidence = calculate_baseline_confidence(investigation)
    investigation_summary = build_prompt(investigation, baseline, evidence)

    user_prompt = _USER_TEMPLATE.format(
        question=question,
        context=context or "default cluster",
        question_type=intent.get("question_type", "general"),
        target_resource=intent.get("target_resource") or "none specified",
        target_namespace=intent.get("target_namespace", "all"),
        keywords=", ".join(intent.get("keywords", [])) or "none",
        investigation_summary=investigation_summary[:3000],
    )

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]

    try:
        raw = call_llm(messages)
        return _validate(parse_llm_json(raw))
    except Exception as exc:
        logger.warning(f"Answer generation failed ({exc}), using fallback")
        return _fallback()


def _validate(result: dict) -> dict:
    result.setdefault("answer", "Unable to generate an answer. Please try again.")
    result.setdefault("kubectl_commands", [])
    result.setdefault("confidence", 50)
    result.setdefault("severity", "medium")

    if result["severity"] not in _VALID_SEVERITIES:
        result["severity"] = "medium"

    result["confidence"] = max(0, min(100, int(result.get("confidence", 50))))
    return result


def _fallback() -> dict:
    return {
        "answer": (
            "I couldn't analyze the cluster data to answer your question. "
            "Please verify the cluster is reachable and try again."
        ),
        "kubectl_commands": [],
        "confidence": 0,
        "severity": "low",
    }
