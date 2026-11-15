#!/usr/bin/env bash
# End-to-end test: break a kind cluster on purpose and check KubeSift explains every failure.
# Needs: kind, kubectl, docker, and kubesift installed (pip install -e .).
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CLUSTER="${CLUSTER:-kubesift-e2e}"

if ! kind get clusters | grep -qx "$CLUSTER"; then
  kind create cluster --name "$CLUSTER" --wait 120s
fi
kubectl config use-context "kind-$CLUSTER"

"$ROOT/test-scenarios/apply-scenario.sh" all
echo "Waiting 150s for failures to develop (crash loops, OOM kills, pull back-offs)..."
sleep 150

kubesift diagnose -n default --no-llm -o json > "$ROOT/e2e-report.json" || true
kubesift diagnose -n default --no-llm -v || true
python "$ROOT/scripts/e2e_check.py" "$ROOT/e2e-report.json" "$ROOT/test-scenarios/expected.json"

echo "--- preflight: a change that needs a Secret the cluster does not have must be blocked"
if kubesift preflight "$ROOT/test-scenarios/07-missing-secret.yaml" -n preflight-check -o markdown > preflight.md; then
  cat preflight.md; echo "preflight should have failed"; exit 1
fi
grep -q "preflight-" preflight.md && cat preflight.md

echo "--- time travel: record, change something, diff"
kubesift snapshot record
sleep 3
kubectl scale deployment/selector-test --replicas=2
kubesift diff --since 2s -n default | tee diff.txt
grep -q "selector-test" diff.txt

echo "--- drill: start, wait, reveal, stop"
kubesift drill start crashloop -n kubesift-drill --yes
sleep 60
kubesift drill reveal crashloop -n kubesift-drill | tee drill.txt
grep -q "KubeSift caught it" drill.txt
kubesift drill stop -n kubesift-drill
