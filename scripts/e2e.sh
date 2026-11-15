#!/usr/bin/env bash
# End-to-end test: break a kind cluster on purpose and check KubeOracle explains every failure.
# Needs: kind, kubectl, docker, and kubeoracle installed (pip install -e .).
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CLUSTER="${CLUSTER:-kubeoracle-e2e}"

if ! kind get clusters | grep -qx "$CLUSTER"; then
  kind create cluster --name "$CLUSTER" --wait 120s
fi
kubectl config use-context "kind-$CLUSTER"

"$ROOT/test-scenarios/apply-scenario.sh" all
echo "Waiting 150s for failures to develop (crash loops, OOM kills, pull back-offs)..."
sleep 150

kubeoracle diagnose -n default --no-llm -o json > "$ROOT/e2e-report.json" || true
kubeoracle diagnose -n default --no-llm -v || true
python "$ROOT/scripts/e2e_check.py" "$ROOT/e2e-report.json" "$ROOT/test-scenarios/expected.json"
