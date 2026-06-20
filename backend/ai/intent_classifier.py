"""
Intent classifier — converts a natural language Kubernetes question into a
structured investigation plan (question type, target resource, domains needed).
"""

from loguru import logger
from ai.llm_client import call_llm, parse_llm_json

SYSTEM_PROMPT = """You are a Kubernetes intent classifier.
Convert a natural language question into a structured investigation plan.

The 11 available inspection domains are:
  pods, logs, events, deployments, network, nodes, resources,
  config_secrets, probes, rbac, scheduling

Classification rules:
- question_type: diagnostic (something broken) | readiness (ready for prod?) |
  performance (cpu/memory/speed) | security (rbac/secrets/auth) | general
- target_resource: exact pod/deployment/service name if mentioned, otherwise null
- target_namespace: explicit namespace if mentioned, otherwise "all"
- relevant_domains: only the 2-6 domains needed to answer this specific question
- keywords: important terms extracted from the question
- focused: true if a specific named resource was mentioned

Respond with ONLY a valid JSON object. No markdown, no prose."""

_USER_TEMPLATE = """Classify this Kubernetes question:

"{question}"

Return exactly this JSON (no other text):
{{
  "question_type": "diagnostic",
  "target_resource": null,
  "target_namespace": "all",
  "relevant_domains": ["pods", "logs", "events"],
  "keywords": ["keyword1", "keyword2"],
  "focused": false
}}"""

_VALID_DOMAINS = frozenset({
    "pods", "logs", "events", "deployments", "network",
    "nodes", "resources", "config_secrets", "probes", "rbac", "scheduling",
})
_VALID_TYPES = frozenset({"diagnostic", "readiness", "performance", "security", "general"})


def classify_intent(question: str) -> dict:
    """Parse a natural language question into an investigation intent."""
    logger.info(f"Classifying intent: {question[:100]}")
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": _USER_TEMPLATE.format(question=question)},
    ]
    try:
        raw = call_llm(messages)
        intent = _validate(parse_llm_json(raw))
        logger.info(
            f"Intent: type={intent['question_type']}  focused={intent['focused']}  "
            f"domains={intent['relevant_domains']}"
        )
        return intent
    except Exception as exc:
        logger.warning(f"Intent classification failed ({exc}), using keyword fallback")
        return _keyword_fallback(question)


def _validate(intent: dict) -> dict:
    intent.setdefault("question_type", "general")
    intent.setdefault("target_resource", None)
    intent.setdefault("target_namespace", "all")
    intent.setdefault("relevant_domains", ["pods", "logs", "events", "deployments"])
    intent.setdefault("keywords", [])
    intent.setdefault("focused", False)

    if intent["question_type"] not in _VALID_TYPES:
        intent["question_type"] = "general"

    intent["relevant_domains"] = [d for d in intent["relevant_domains"] if d in _VALID_DOMAINS]
    if not intent["relevant_domains"]:
        intent["relevant_domains"] = ["pods", "logs", "events", "deployments"]

    intent["focused"] = bool(intent.get("target_resource"))
    return intent


def _keyword_fallback(question: str) -> dict:
    q = question.lower()

    if any(w in q for w in ["network", "service", "ingress", "connect", "reach", "dns"]):
        return _intent("diagnostic", ["pods", "events", "network", "deployments"])
    if any(w in q for w in ["memory", "cpu", "resource", "limit", "quota", "oom"]):
        return _intent("performance", ["pods", "resources", "nodes"])
    if any(w in q for w in ["rbac", "permission", "secret", "security", "auth", "forbidden"]):
        return _intent("security", ["rbac", "config_secrets", "pods"])
    if any(w in q for w in ["ready", "deploy", "production", "probe", "health"]):
        return _intent("readiness", ["pods", "deployments", "probes", "resources"])
    if any(w in q for w in ["pending", "schedule", "node", "capacity", "taint"]):
        return _intent("diagnostic", ["pods", "nodes", "scheduling", "events"])

    return _intent("general", ["pods", "logs", "events", "deployments"])


def _intent(question_type: str, domains: list[str]) -> dict:
    return {
        "question_type": question_type,
        "target_resource": None,
        "target_namespace": "all",
        "relevant_domains": domains,
        "keywords": [],
        "focused": False,
    }
