#!/bin/bash
# Usage: ./apply-scenario.sh <scenario-number>
# Example: ./apply-scenario.sh 1

set -e

SCENARIO=$1
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

case $SCENARIO in
  1)
    kubectl apply -f "$SCRIPT_DIR/01-crashloop-missing-env.yaml"
    echo "Scenario 1: CrashLoopBackOff (missing DATABASE_URL env var)"
    ;;
  2)
    kubectl apply -f "$SCRIPT_DIR/02-imagepull-wrong-tag.yaml"
    echo "Scenario 2: ImagePullBackOff (invalid image tag)"
    ;;
  3)
    kubectl apply -f "$SCRIPT_DIR/03-oomkilled-low-memory.yaml"
    echo "Scenario 3: OOMKilled (memory limit too low)"
    ;;
  4)
    kubectl apply -f "$SCRIPT_DIR/04-service-selector-mismatch.yaml"
    echo "Scenario 4: Service selector mismatch (no endpoints)"
    ;;
  5)
    kubectl apply -f "$SCRIPT_DIR/05-missing-probe-crashloop.yaml"
    echo "Scenario 5: Missing probe + crash loop"
    ;;
  6)
    kubectl apply -f "$SCRIPT_DIR/06-pending-insufficient-resources.yaml"
    echo "Scenario 6: Pending pod (999Gi memory request — unschedulable)"
    ;;
  *)
    echo "Usage: ./apply-scenario.sh <1-6>"
    echo ""
    echo "  1  CrashLoopBackOff — missing DATABASE_URL env var"
    echo "  2  ImagePullBackOff — invalid image tag"
    echo "  3  OOMKilled        — memory limit too low (32Mi)"
    echo "  4  No endpoints     — service selector mismatch"
    echo "  5  CrashLoop        — app crashes after 5s, no probe"
    echo "  6  Pending          — requests 999Gi memory"
    exit 1
    ;;
esac

echo ""
echo "Wait ~30 seconds for the pod to reach a failing state, then click 'Investigate Cluster'."
