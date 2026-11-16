"""Score an LLM on KubeSift's demo broken cluster: does it find the real causes and cite real evidence?

Usage:
  python scripts/eval_ai.py --provider anthropic                    # needs ANTHROPIC_API_KEY
  python scripts/eval_ai.py --provider ollama --model llama3.1      # local; slow without a GPU
  python scripts/eval_ai.py --provider openrouter --model openai/gpt-5 --json results.json

What is measured (the deterministic checks already found every fault; this grades the explanation):
  coverage   share of the five root problems that some root cause cites as evidence
  cited      share of root causes that cite at least one real finding (the rest are capped at 40% confidence)
  questions  three "why is X failing?" questions answered with the right evidence

Exit code 1 when coverage or cited is below its threshold, so it can gate a CI job.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tests"))

from fixtures import broken_cluster  # noqa: E402

from kubesift.analysis import engine  # noqa: E402
from kubesift.config import load_settings  # noqa: E402

# The five faults an on-call engineer must not miss in the demo cluster, keyed by the check that proves each.
ROOT_PROBLEMS = {
    "pod-crashloop": "checkout crash loops after the 1.5 rollout removed DATABASE_URL",
    "pod-oomkilled": "api is OOMKilled at a 64Mi limit",
    "webhook-unavailable": "policy webhook points at a missing Service and blocks changes",
    "node-not-ready": "node-2 is NotReady",
    "config-ref-missing": "reports needs a ConfigMap that does not exist",
}

QUESTIONS = [
    ("why is checkout failing?", "pod-crashloop", "DATABASE_URL"),
    ("why does the api deployment keep restarting?", "pod-oomkilled", "memory"),
    ("why does the web service have no endpoints?", "service-no-endpoints", "selector"),
]


def evaluate(settings: Any) -> dict[str, Any]:
    rules = engine.investigate(settings, broken_cluster(), use_llm=False)
    by_check: dict[str, set[str]] = {}
    for incident in rules.incidents:
        ids = {f.fingerprint for f in incident.findings}
        for f in incident.findings:
            by_check.setdefault(f.check, set()).update(ids)

    started = time.monotonic()
    report = engine.investigate(settings, broken_cluster(), use_llm=True)
    diagnose_s = time.monotonic() - started
    diagnosis = report.diagnosis
    if diagnosis is None or diagnosis.generated_by == "rules":
        return {"error": "the LLM did not answer: " + "; ".join(report.warnings[-2:]), "diagnose_seconds": diagnose_s}

    cited_ids = {e for rc in diagnosis.root_causes for e in rc.evidence}
    found = {check: bool(by_check.get(check, set()) & cited_ids) for check in ROOT_PROBLEMS}
    causes = [{"title": rc.title, "confidence": rc.confidence, "cites": len(rc.evidence)} for rc in diagnosis.root_causes]
    cited_share = sum(1 for rc in diagnosis.root_causes if rc.evidence) / max(1, len(diagnosis.root_causes))

    answers = []
    for question, check, keyword in QUESTIONS:
        t = time.monotonic()
        _, answer = engine.ask(settings, broken_cluster(), question, use_llm=True)
        llm = not str(answer.get("generated_by", "rules")).startswith("rules")
        answers.append({
            "question": question, "llm": llm, "seconds": round(time.monotonic() - t, 1),
            "right_evidence": bool(by_check.get(check, set()) & set(answer.get("evidence") or [])),
            "mentions": keyword.lower() in str(answer.get("answer", "")).lower(),
        })

    return {
        "model": diagnosis.generated_by,
        "diagnose_seconds": round(diagnose_s, 1),
        "coverage": sum(found.values()) / len(found),
        "found": found,
        "cited": round(cited_share, 2),
        "root_causes": causes,
        "questions": answers,
        "questions_right": sum(1 for a in answers if a["right_evidence"]),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--provider", required=True, help="anthropic | openai | openrouter | ollama")
    ap.add_argument("--model", help="model name (default: the provider's default)")
    ap.add_argument("--json", help="also write the results to this file")
    ap.add_argument("--min-coverage", type=float, default=0.6)
    ap.add_argument("--min-cited", type=float, default=0.8)
    args = ap.parse_args()

    settings = load_settings(llm_provider=args.provider, llm_model=args.model)
    result = evaluate(settings)
    if args.json:
        Path(args.json).write_text(json.dumps(result, indent=2), encoding="utf-8")
    if "error" in result:
        print(f"FAIL {result['error']}")
        return 1

    print(f"model      {result['model']}  ({result['diagnose_seconds']}s for the diagnosis)")
    print(f"coverage   {result['coverage']:.0%} of the root problems cited  (min {args.min_coverage:.0%})")
    for check, ok in result["found"].items():
        print(f"  {'+' if ok else '-'} {ROOT_PROBLEMS[check]}")
    print(f"cited      {result['cited']:.0%} of root causes cite real evidence  (min {args.min_cited:.0%})")
    for rc in result["root_causes"]:
        print(f"  [{rc['confidence']:>3}%] {rc['title'][:90]}  ({rc['cites']} citation(s))")
    print(f"questions  {result['questions_right']}/{len(result['questions'])} answered with the right evidence")
    for a in result["questions"]:
        print(f"  {'+' if a['right_evidence'] else '-'} {a['question']}  ({a['seconds']}s{'' if a['llm'] else ', rules fallback'})")
    ok = result["coverage"] >= args.min_coverage and result["cited"] >= args.min_cited
    print("PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
