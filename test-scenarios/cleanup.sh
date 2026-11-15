#!/usr/bin/env bash
# Remove all test scenario objects
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
for f in "$DIR"/*.yaml; do kubectl delete -f "$f" --ignore-not-found; done
echo "All test scenarios cleaned up."
