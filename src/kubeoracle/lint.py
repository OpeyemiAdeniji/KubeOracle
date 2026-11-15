"""Offline checks on manifest files, Helm charts and Kustomize output, for CI and pre-commit."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import yaml

from kubeoracle.checks.base import run_checks
from kubeoracle.cluster.snapshot import SOURCE, Snapshot
from kubeoracle.config import Settings
from kubeoracle.models import Finding


class LintError(Exception):
    pass


def _docs_from_text(text: str, source: str) -> list[dict[str, Any]]:
    docs = []
    try:
        for i, doc in enumerate(yaml.safe_load_all(text)):
            if isinstance(doc, dict) and doc.get("kind"):
                doc.setdefault("metadata", {}).setdefault("annotations", {})
                if doc["metadata"]["annotations"] is None:
                    doc["metadata"]["annotations"] = {}
                doc["metadata"]["annotations"][SOURCE] = f"{source}#{i}"
                docs.append(doc)
    except yaml.YAMLError as exc:
        raise LintError(f"{source}: invalid YAML: {exc}") from exc
    return docs


def _render_chart(path: Path, values: list[str]) -> str:
    helm = shutil.which("helm")
    if not helm:
        raise LintError(f"{path} is a Helm chart; install helm or pipe `helm template` output into `kubeoracle lint -`.")
    cmd = [helm, "template", "kubeoracle-lint", str(path)] + [x for v in values for x in ("-f", v)]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if proc.returncode != 0:
        raise LintError(f"helm template failed: {proc.stderr.strip()[:500]}")
    return proc.stdout


def _render_kustomize(path: Path) -> str:
    kubectl = shutil.which("kubectl")
    if not kubectl:
        raise LintError(f"{path} is a Kustomize directory; install kubectl or pipe `kustomize build` into `kubeoracle lint -`.")
    proc = subprocess.run([kubectl, "kustomize", str(path)], capture_output=True, text=True, timeout=120)
    if proc.returncode != 0:
        raise LintError(f"kustomize build failed: {proc.stderr.strip()[:500]}")
    return proc.stdout


def load_documents(paths: list[str], values: list[str] | None = None) -> list[dict[str, Any]]:
    docs: list[dict[str, Any]] = []
    for raw in paths:
        if raw == "-":
            docs.extend(_docs_from_text(sys.stdin.read(), "stdin"))
            continue
        p = Path(raw)
        if not p.exists():
            raise LintError(f"{raw}: no such file or directory")
        if p.is_dir() and (p / "Chart.yaml").exists():
            docs.extend(_docs_from_text(_render_chart(p, values or []), str(p)))
        elif p.is_dir() and any((p / k).exists() for k in ("kustomization.yaml", "kustomization.yml", "Kustomization")):
            docs.extend(_docs_from_text(_render_kustomize(p), str(p)))
        elif p.is_dir():
            for f in sorted(list(p.rglob("*.yaml")) + list(p.rglob("*.yml"))):
                if any(part in (".git", "node_modules", "templates") for part in f.parts):
                    continue
                docs.extend(_docs_from_text(f.read_text(encoding="utf-8"), str(f)))
        else:
            docs.extend(_docs_from_text(p.read_text(encoding="utf-8"), str(p)))
    return docs


def lint_text(text: str, settings: Settings, target_version: str | None = None) -> list[Finding]:
    return run_checks(Snapshot.from_manifests(_docs_from_text(text, "input")), settings, target_version=target_version)


def lint(paths: list[str], settings: Settings, target_version: str | None = None,
         values: list[str] | None = None) -> list[Finding]:
    snap = Snapshot.from_manifests(load_documents(paths, values))
    return run_checks(snap, settings, target_version=target_version)
