"""Write the krew plugin manifest (sift.yaml) for a release from the built archives' .sha256 files.

    python scripts/krew_manifest.py v2.1.6 dist/      # -> dist/sift.yaml

The first version is submitted to kubernetes-sigs/krew-index by hand (see docs/krew.md); later releases can be
proposed automatically by krew-release-bot.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO = "https://github.com/OpeyemiAdeniji/KubeSift"
PLATFORMS = [("linux", "amd64"), ("linux", "arm64"), ("darwin", "amd64"), ("darwin", "arm64"), ("windows", "amd64")]

HEADER = """apiVersion: krew.googlecontainertools.github.com/v1alpha2
kind: Plugin
metadata:
  name: sift
spec:
  version: {version}
  homepage: {repo}
  shortDescription: Explain why workloads fail, with evidence and fixes
  description: |
    Finds what is broken in a cluster or namespace (crash loops, image pulls, OOM kills, pending pods,
    Services without endpoints, RBAC and policy denials, failing probes, certificates and more), groups the
    symptoms into incidents, links each one to the rollout, config edit or Helm/Argo CD sync that came just
    before it, and prints the evidence, the likely fix and the kubectl commands to run.
    Read-only; works without an AI key. Optional AI explanations with Ollama, Anthropic or OpenAI-compatible
    endpoints. Also lints manifests and checks upgrades.
  caveats: |
    Run `kubectl sift diagnose` (or `-n NAMESPACE`). It needs read access (get/list) to the objects it checks.
    The web dashboard and the MCP server for AI assistants come with `pip install "kubesift[all]"`.
  platforms:
"""

PLATFORM = """  - selector:
      matchLabels:
        os: {os}
        arch: {arch}
    uri: {repo}/releases/download/{version}/{archive}
    sha256: "{sha256}"
    files:
      - from: kubectl-sift/*
        to: .
    bin: {bin}
"""


def manifest(version: str, dist: Path) -> str:
    out = HEADER.format(version=version, repo=REPO)
    for os_, arch in PLATFORMS:
        archive = f"kubectl-sift_{os_}_{arch}.{'zip' if os_ == 'windows' else 'tar.gz'}"
        sha_file = dist / f"{archive}.sha256"
        if not sha_file.exists():
            sys.exit(f"missing {sha_file}: every platform must be built before the manifest is written")
        sha256 = sha_file.read_text(encoding="utf-8").split()[0]
        out += PLATFORM.format(os=os_, arch=arch, repo=REPO, version=version, archive=archive, sha256=sha256,
                               bin="kubectl-sift.exe" if os_ == "windows" else "kubectl-sift")
    return out


if __name__ == "__main__":
    version, dist = sys.argv[1], Path(sys.argv[2])
    if not version.startswith("v"):
        sys.exit("version must be the release tag, e.g. v2.1.6")
    (dist / "sift.yaml").write_text(manifest(version, dist), encoding="utf-8", newline="\n")
    print(dist / "sift.yaml")
