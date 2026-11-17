#!/usr/bin/env bash
# Every `uses: owner/repo@version` in the workflows and action.yml must exist. A missing version is only noticed
# when the workflow runs, and for the release workflow that is after part of the release has shipped.
set -euo pipefail
refs=$(grep -rhoE 'uses: *[A-Za-z0-9_.-]+/[A-Za-z0-9_./-]+@[A-Za-z0-9_./-]+' "${@:-.github/workflows}" action.yml \
       | sed -E 's/uses: *//' | sort -u)
missing=0
for ref in $refs; do
  repo=$(echo "${ref%@*}" | cut -d/ -f1-2)   # owner/repo/sub/path@v -> owner/repo
  version=${ref##*@}
  if git ls-remote --exit-code "https://github.com/${repo}" "refs/tags/${version}" "refs/heads/${version}" >/dev/null 2>&1 \
     || [[ "$version" =~ ^[0-9a-f]{40}$ ]]; then
    echo "ok       $ref"
  else
    echo "MISSING  $ref"; missing=1
  fi
done
exit $missing
