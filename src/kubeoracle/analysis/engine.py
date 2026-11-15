"""The investigation pipeline: collect -> check -> correlate -> explain."""

from __future__ import annotations

import json
import logging
import re
from datetime import timedelta
from typing import Any

from kubeoracle import runbooks
from kubeoracle.analysis.changes import detect_changes
from kubeoracle.analysis.correlate import build_incidents, link_changes, pod_counts
from kubeoracle.analysis.describe import ALIASES, summarize
from kubeoracle.analysis.llm import LLMError, get_provider
from kubeoracle.checks.base import run_checks
from kubeoracle.cluster.client import KubeClient
from kubeoracle.cluster.snapshot import KIND_TO_KEY, Snapshot, collect
from kubeoracle.config import Settings
from kubeoracle.models import Category, Diagnosis, Finding, Report, RootCause
from kubeoracle.redact import redact
from kubeoracle.store import Store
from kubeoracle.util import meta

log = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are KubeOracle, a senior Kubernetes SRE helping an on-call engineer.
You receive findings that deterministic checks proved from live cluster data, recent changes, and resource summaries.

Rules:
- Base every conclusion on the provided evidence. Cite the fingerprints of the findings you rely on in `evidence`.
- Never invent resource names, versions, or log lines. If the data does not show the cause, say what is missing and how to get it.
- Separate the root cause from its symptoms: a bad rollout, a config edit or a broken node often explains several findings.
- When a recent change lines up with the start of a problem, say so and give the rollback command if one is provided.
- Prefer read-only kubectl commands. Mark anything that changes the cluster with "(changes the cluster)".
- Confidence: 80-100 only when evidence directly proves the cause; 50-79 when it is the most likely explanation; below 50 when guessing.
- Be concise and concrete. Write for someone who is tired and needs the next step."""

DIAGNOSIS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["summary", "root_causes", "next_checks"],
    "properties": {
        "summary": {"type": "string", "description": "Two or three sentences: what is broken and why."},
        "root_causes": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["title", "explanation", "confidence", "evidence", "fix_steps", "commands"],
                "properties": {
                    "title": {"type": "string"},
                    "explanation": {"type": "string"},
                    "confidence": {"type": "integer"},
                    "evidence": {"type": "array", "items": {"type": "string"}},
                    "fix_steps": {"type": "array", "items": {"type": "string"}},
                    "commands": {"type": "array", "items": {"type": "string"}},
                },
            },
        },
        "next_checks": {"type": "array", "items": {"type": "string"}},
    },
}

ANSWER_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["answer", "confidence", "evidence", "commands", "follow_up"],
    "properties": {
        "answer": {"type": "string"},
        "confidence": {"type": "integer"},
        "evidence": {"type": "array", "items": {"type": "string"}},
        "commands": {"type": "array", "items": {"type": "string"}},
        "follow_up": {"type": "array", "items": {"type": "string"}},
    },
}

_MAX_CONTEXT = 60_000


def snapshot_for(settings: Settings, namespace: str | None, context: str | None = None,
                 with_logs: bool = True, kinds: set[str] | None = None) -> Snapshot:
    client = KubeClient(kubeconfig=settings.kubeconfig, context=context or settings.context,
                        timeout=settings.request_timeout)
    return collect(client, namespace=namespace, max_items=settings.max_items_per_kind,
                   with_logs=with_logs, kinds=kinds)


def investigate(
    settings: Settings,
    snap: Snapshot,
    *,
    use_llm: bool = True,
    window: timedelta = timedelta(hours=6),
    categories: list[Category] | None = None,
    only: list[str] | None = None,
    include_risks: bool = True,
    store: Store | None = None,
) -> Report:
    findings = run_checks(snap, settings, only=only, categories=categories)
    if not include_risks:
        findings = [f for f in findings if f.kind == "problem"]
    changes = detect_changes(snap, window)
    link_changes(snap, findings, changes)
    if store:
        store.attach_notes(findings)
    runbooks.attach(runbooks.load(settings.runbooks_dir), findings)
    report = Report(
        cluster=snap.context, namespace=snap.namespace, collected_at=snap.collected_at,
        server_version=snap.server_version, findings=findings, incidents=build_incidents(snap, findings),
        changes=changes, warnings=list(snap.warnings),
    )
    report.diagnosis = explain(settings, report, use_llm=use_llm)
    return report


# ----------------------------------------------------------------- diagnosis
def rules_diagnosis(report: Report) -> Diagnosis:
    problems = [i for i in report.incidents]
    if not problems:
        risks = [f for f in report.findings if f.kind == "risk"]
        msg = "No active problems found."
        if risks:
            msg += f" {len(risks)} configuration risk(s) worth fixing before they cause an incident."
        return Diagnosis(summary=msg, generated_by="rules")
    causes = []
    for inc in problems[:5]:
        top = inc.findings[0]
        steps = [top.remediation] if top.remediation else []
        cmds = list(top.commands)
        conf = 85 if top.severity.rank >= 3 else 70
        explanation = top.message
        if inc.likely_trigger:
            t = inc.likely_trigger
            extra = [d for d in t.details[1:4]]
            explanation += f" This started after: {t.summary}" + (f" ({'; '.join(extra)})." if extra else ".")
            if inc.likely_trigger.rollback_command:
                steps.insert(0, "If the change caused it, roll back first, then investigate.")
                cmds.insert(0, inc.likely_trigger.rollback_command + "   # (changes the cluster)")
        causes.append(RootCause(title=inc.title, explanation=explanation, confidence=conf,
                                evidence=[f.fingerprint for f in inc.findings], fix_steps=steps, commands=cmds[:4]))
    worst = problems[0]
    summary = f"{len(problems)} incident(s). Most severe: {worst.title}."
    if worst.likely_trigger:
        summary += f" It lines up with a recent change: {worst.likely_trigger.summary}."
    return Diagnosis(summary=summary, root_causes=causes, generated_by="rules")


def _finding_payload(f: Finding) -> dict[str, Any]:
    return {
        "fingerprint": f.fingerprint, "check": f.check, "severity": f.severity.value, "resource": str(f.resource),
        "owner": str(f.owner) if f.owner else None, "node": f.node, "message": f.message,
        "evidence": f.evidence[:8], "suggested_fix": f.remediation, "commands": f.commands[:3],
        "started": f.since.isoformat() if f.since else None,
        "related_changes": [c.summary + (f" [rollback: {c.rollback_command}]" if c.rollback_command else "")
                            for c in f.related_changes],
        "team_notes": f.notes[:3],
    }


def _context_json(report: Report, extra: dict[str, Any] | None = None) -> str:
    payload: dict[str, Any] = {
        "cluster": report.cluster, "namespace": report.namespace or "all", "kubernetes": report.server_version,
        "incidents": [{"title": i.title, "layer": i.layer, "severity": i.severity.value,
                       "findings": [_finding_payload(f) for f in i.findings[:6]]} for i in report.incidents[:12]],
        "risks": [{"fingerprint": f.fingerprint, "check": f.check, "resource": str(f.resource), "message": f.message}
                  for f in report.findings if f.kind == "risk"][:25],
        "recent_changes": [{"time": c.time.isoformat(), "summary": c.summary, "details": c.details[:5],
                            "rollback": c.rollback_command} for c in report.changes[:15]],
        "data_gaps": report.warnings[:10],
    }
    if extra:
        payload.update(extra)
    text = redact(json.dumps(payload, default=str))
    if len(text) > _MAX_CONTEXT:
        payload["risks"] = payload["risks"][:5]
        payload["incidents"] = payload["incidents"][:6]
        text = redact(json.dumps(payload, default=str))[:_MAX_CONTEXT]
    return text


def explain(settings: Settings, report: Report, use_llm: bool = True) -> Diagnosis:
    base = rules_diagnosis(report)
    if not use_llm or not report.incidents:
        return base
    try:
        provider = get_provider(settings)
    except LLMError as exc:
        report.warnings.append(f"LLM disabled: {exc}")
        return base
    if provider is None:
        return base
    if not settings.send_logs_to_llm:
        for f in report.findings:
            f.evidence = [e for e in f.evidence if not e.startswith("log ")]
    try:
        data = provider.complete_json(SYSTEM_PROMPT, "Diagnose this cluster state.\n\n" + _context_json(report),
                                      DIAGNOSIS_SCHEMA)
        return _validated(data, report, f"{provider.name}:{settings.resolved_model()}")
    except (LLMError, KeyError, TypeError, ValueError) as exc:
        report.warnings.append(f"LLM analysis failed, showing rule-based diagnosis: {exc}")
        return base


def _validated(data: dict[str, Any], report: Report, by: str) -> Diagnosis:
    known = {f.fingerprint for f in report.findings}
    causes = []
    for rc in data.get("root_causes") or []:
        evidence = [e for e in rc.get("evidence") or [] if e in known]
        conf = max(0, min(100, int(rc.get("confidence", 0))))
        if not evidence:
            conf = min(conf, 40)  # unsupported claims cannot be confident
        causes.append(RootCause(title=str(rc.get("title", ""))[:200], explanation=str(rc.get("explanation", "")),
                                confidence=conf, evidence=evidence,
                                fix_steps=[str(s) for s in rc.get("fix_steps") or []][:8],
                                commands=[str(c) for c in rc.get("commands") or []][:6]))
    causes.sort(key=lambda c: -c.confidence)
    return Diagnosis(summary=str(data.get("summary", "")), root_causes=causes,
                     next_checks=[str(x) for x in data.get("next_checks") or []][:6], generated_by=by)


# ------------------------------------------------------------------------ ask
_TOPICS: list[tuple[re.Pattern[str], list[Category]]] = [
    (re.compile(r"crash|restart|exit|oom|memory|killed|error|fail|down|broken|not working", re.I),
     [Category.WORKLOAD, Category.CONFIG, Category.RESOURCES]),
    (re.compile(r"pending|schedul|capacity|node|taint", re.I), [Category.SCHEDULING, Category.NODE, Category.RESOURCES]),
    (re.compile(r"network|dns|service|endpoint|ingress|gateway|503|502|504|timeout|connect|traffic|route", re.I),
     [Category.NETWORK, Category.PLATFORM]),
    (re.compile(r"volume|pvc|disk|storage|mount", re.I), [Category.STORAGE]),
    (re.compile(r"secur|rbac|privileg|root|secret|cert|tls", re.I), [Category.SECURITY]),
    (re.compile(r"cost|money|spend|waste|idle|gpu|right-?siz|over-?provision", re.I), [Category.COST, Category.RESOURCES]),
    (re.compile(r"upgrade|deprecat|version|removed|eol|retire", re.I), [Category.UPGRADE]),
    (re.compile(r"ready for prod|production ready|best practice|reliab|readiness|probe", re.I),
     [Category.RELIABILITY, Category.SECURITY, Category.RESOURCES]),
]


def mentioned_resources(snap: Snapshot, question: str) -> list[dict[str, Any]]:
    """Objects whose name appears in the question (e.g. 'why is checkout-api failing?')."""
    words = set(re.findall(r"[a-z0-9][a-z0-9.-]{2,}", question.lower()))
    hits = []
    for key in ("deployments", "statefulsets", "daemonsets", "cronjobs", "jobs", "services", "ingresses", "pods",
                "nodes", "pvcs"):
        for obj in snap.all(key):
            name = (meta(obj).get("name") or "").lower()
            if name in words:
                hits.append(obj)
    # pods matched only by name prefix of a mentioned workload are noise; keep the top-level objects first
    hits.sort(key=lambda o: o.get("kind") == "Pod")
    return hits[:6]


def mentioned_namespace(question: str, namespaces: list[str]) -> str | None:
    m = re.search(r"(?:namespace|ns|-n)\s+([a-z0-9-]+)", question, re.I)
    if m and m.group(1) in namespaces:
        return m.group(1)
    for ns in namespaces:
        if re.search(rf"\b{re.escape(ns)}\b", question) and ns not in ("default",):
            return ns
    return None


def ask(settings: Settings, snap: Snapshot, question: str, use_llm: bool = True,
        store: Store | None = None) -> tuple[Report, dict[str, Any]]:
    """Answer a free-form question about the cluster. Returns (report, answer)."""
    targets = mentioned_resources(snap, question)
    cats: list[Category] = []
    for pattern, c in _TOPICS:
        if pattern.search(question):
            cats.extend(x for x in c if x not in cats)
    report = investigate(settings, snap, use_llm=False, store=store)
    report.question = question

    relevant = report.findings
    if targets:
        names = {(meta(t).get("namespace"), meta(t).get("name")) for t in targets}
        relevant = [f for f in report.findings
                    if (f.resource.namespace, f.resource.name) in names
                    or (f.owner and (f.owner.namespace, f.owner.name) in names)]
    elif cats:
        relevant = [f for f in report.findings if f.category in cats] or report.findings

    summaries = [summarize(snap, t) for t in targets]
    answer = _rules_answer(question, relevant, summaries, targets)
    provider = None
    if use_llm:
        try:
            provider = get_provider(settings)
        except LLMError as exc:
            report.warnings.append(f"LLM disabled: {exc}")
    if provider:
        ctx = _context_json(report, {"question": question, "relevant_findings": [_finding_payload(f) for f in relevant[:20]],
                                     "resources_mentioned": summaries})
        try:
            data = provider.complete_json(
                SYSTEM_PROMPT + "\nAnswer the engineer's question directly first, then the supporting detail.",
                f"Question: {question}\n\nCluster data:\n{ctx}", ANSWER_SCHEMA)
            known = {f.fingerprint for f in report.findings}
            answer = {
                "answer": str(data.get("answer", "")),
                "confidence": max(0, min(100, int(data.get("confidence", 0)))),
                "evidence": [e for e in data.get("evidence") or [] if e in known],
                "commands": [str(c) for c in data.get("commands") or []][:6],
                "follow_up": [str(c) for c in data.get("follow_up") or []][:5],
                "generated_by": f"{provider.name}:{settings.resolved_model()}",
            }
        except (LLMError, KeyError, TypeError, ValueError) as exc:
            report.warnings.append(f"LLM answer failed, showing rule-based answer: {exc}")
    report.findings = relevant
    report.incidents = [i for i in report.incidents if any(f in relevant for f in i.findings)]
    report.diagnosis = rules_diagnosis(report)
    return report, answer


def _rules_answer(question: str, relevant: list[Finding], summaries: list[dict[str, Any]],
                  targets: list[dict[str, Any]]) -> dict[str, Any]:
    problems = [f for f in relevant if f.kind == "problem"]
    if problems:
        top = problems[0]
        text = f"{top.resource}: {top.message}"
        if top.related_changes:
            text += f" This began after: {top.related_changes[0].summary}."
        if len(problems) > 1:
            text += f" {len(problems) - 1} more problem(s) are listed below."
        return {"answer": text, "confidence": 80, "evidence": [f.fingerprint for f in problems[:5]],
                "commands": top.commands[:3], "follow_up": [top.remediation] if top.remediation else [],
                "generated_by": "rules"}
    if targets:
        names = ", ".join(f"{s['kind']}/{s['name']}" for s in summaries)
        risks = [f for f in relevant if f.kind == "risk"]
        text = f"No active problems found on {names}."
        if risks:
            text += f" {len(risks)} configuration risk(s) found (listed below)."
        return {"answer": text, "confidence": 70, "evidence": [f.fingerprint for f in risks[:5]], "commands": [],
                "follow_up": [], "generated_by": "rules"}
    return {"answer": "No active problems match this question." + (
        f" {len(relevant)} related risk(s) listed below." if relevant else ""),
        "confidence": 60, "evidence": [], "commands": [], "follow_up": [
            "Name the workload in your question (e.g. 'why is checkout-api failing in shop?') for a focused answer."],
        "generated_by": "rules"}


def explain_resource(settings: Settings, snap: Snapshot, kind: str, namespace: str | None, name: str) -> dict[str, Any]:
    key = KIND_TO_KEY.get(ALIASES.get(kind.lower(), kind))
    obj = snap.get(key, namespace, name) if key else None
    if obj is None:
        return {"error": f"{kind}/{name} not found" + (f" in namespace {namespace}" if namespace else "")}
    report = investigate(settings, snap, use_llm=False)
    related = [f for f in report.findings
               if (f.resource.namespace, f.resource.name) == (namespace, name)
               or (f.owner and (f.owner.namespace, f.owner.name, f.owner.kind) == (namespace, name, obj.get("kind")))]
    pods = []
    if obj.get("kind") in ("Deployment", "StatefulSet", "DaemonSet", "Job"):
        pods = [summarize(snap, p, events=4) for p in snap.in_namespace("pods", namespace)
                if str(snap.owner_of(p)) == f"{obj.get('kind')}/{namespace}/{name}"][:5]
    return {"resource": summarize(snap, obj), "pods": pods,
            "findings": [f.model_dump(mode="json") for f in related],
            "changes": [c.model_dump(mode="json") for c in report.changes
                        if c.resource.name == name and c.resource.namespace == namespace],
            "counts": pod_counts(snap)}
