"""Assert that `kubesift diagnose -o json` found every problem the test scenarios create.

Usage: kubesift diagnose -n default --no-llm -o json > report.json
       python scripts/e2e_check.py report.json test-scenarios/expected.json
"""

import json
import sys
from pathlib import Path


def main(report_path: str, expected_path: str) -> int:
    report = json.loads(Path(report_path).read_text(encoding="utf-8"))
    expected = json.loads(Path(expected_path).read_text(encoding="utf-8"))
    found: dict[str, set[str]] = {}
    for f in report["findings"]:
        for ref in (f["resource"], f.get("owner") or {}):
            if ref:
                found.setdefault(ref["name"], set()).add(f["check"])
    failures = 0
    for name, checks in expected.items():
        hit = found.get(name, set()) & set(checks)
        status = "ok  " if hit else "MISS"
        failures += not hit
        print(f"{status} {name:22} expected one of {checks}, got {sorted(found.get(name, []))}")
    rollout = [f for f in report["findings"] if (f.get("owner") or {}).get("name") == "rollout-test"
               and f["check"] == "pod-crashloop"]
    linked = any(c["kind"] == "rollout" for f in rollout for c in f.get("related_changes", []))
    print(f"{'ok  ' if linked else 'MISS'} rollout-test crash linked to its rollout")
    failures += not linked
    print(f"\n{len(expected) + 1 - failures}/{len(expected) + 1} scenario checks passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], sys.argv[2]))
