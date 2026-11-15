#!/usr/bin/env bash
# Usage: ./apply-scenario.sh <number|all>
# Example: ./apply-scenario.sh 3      ./apply-scenario.sh all
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

apply() {
  case "$1" in
    10)
      kubectl apply -f "$DIR/10a-rollout-good.yaml"
      kubectl rollout status deployment/rollout-test --timeout=120s
      kubectl apply -f "$DIR/10b-rollout-bad.yaml"
      ;;
    *)
      file=$(ls "$DIR"/$(printf '%02d' "$1")-*.yaml 2>/dev/null | head -1)
      [ -n "$file" ] || { echo "no scenario $1"; exit 1; }
      kubectl apply -f "$file"
      ;;
  esac
}

if [ "${1:-}" = "all" ]; then
  for n in 1 2 3 4 5 6 7 8 9 10; do apply "$n"; done
else
  apply "${1:?usage: $0 <number|all>}"
fi
