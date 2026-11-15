"""Fire drills: break a dedicated namespace on purpose so on-call engineers can practise.

Drills only ever touch a namespace labelled kubesift.dev/drill=true (created by KubeSift), never existing
application namespaces. `reveal` shows the answer and whether KubeSift caught it; `stop` deletes the namespace.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from importlib import resources
from typing import Any

import yaml

from kubesift.cluster.client import KubeClient, KubeError
from kubesift.preflight import _api_path

DRILL_LABEL = "kubesift.dev/drill"


@dataclass(frozen=True)
class Drill:
    id: str
    title: str
    story: str
    hint: str
    expect: tuple[str, ...]
    files: tuple[str, ...]
    answer: str


DRILLS: dict[str, Drill] = {d.id: d for d in [
    Drill("crashloop", "Checkout keeps restarting", "The checkout service restarts every few seconds after a config clean-up.",
          "Look at the previous container logs, not the current ones.", ("pod-crashloop",),
          ("01-crashloop-missing-env.yaml",), "The app reads DATABASE_URL, which is not set on the container."),
    Drill("image", "New version never starts", "A deploy went out but no new pod became ready.",
          "Describe a pod and read its events.", ("pod-image-pull",), ("02-imagepull-wrong-tag.yaml",),
          "The image tag does not exist in the registry."),
    Drill("oom", "Service dies under load", "Pods disappear and come back every few minutes.",
          "Check lastState.terminated.reason on the container.", ("pod-oomkilled",), ("03-oomkilled-low-memory.yaml",),
          "The container exceeds its memory limit and is OOMKilled."),
    Drill("selector", "Service returns 503", "The Service exists and the pods are healthy, but requests fail.",
          "Compare the Service selector with the pod labels.", ("service-no-endpoints",),
          ("04-service-selector-mismatch.yaml",), "The Service selector has a typo, so it has no endpoints."),
    Drill("pending", "Pods stuck Pending", "A new workload never gets scheduled.",
          "Read the FailedScheduling event.", ("pod-pending",), ("06-pending-insufficient-resources.yaml",),
          "It requests more CPU and memory than any node has."),
    Drill("secret", "CreateContainerConfigError", "Pods are created but containers never start.",
          "The error names a missing object.", ("pod-config-error", "config-ref-missing"), ("07-missing-secret.yaml",),
          "The Secret db-credentials does not exist in the namespace."),
    Drill("volume", "Database never starts", "A stateful workload waits forever.",
          "Look at the PVC, not the pod.", ("pvc-pending",), ("08-pvc-missing-storageclass.yaml",),
          "The PVC asks for a StorageClass that does not exist."),
    Drill("bad-rollout", "It broke right after a deploy", "Everything was fine until the last release.",
          "Ask what changed between the last two revisions.", ("pod-crashloop",),
          ("10a-rollout-good.yaml", "10b-rollout-bad.yaml"),
          "Revision 2 removed API_URL, which the new code needs. Roll back, then fix the manifest."),
]}


def _docs(file: str) -> list[dict[str, Any]]:
    text = resources.files("kubesift.drill_scenarios").joinpath(file).read_text(encoding="utf-8")
    return [d for d in yaml.safe_load_all(text) if isinstance(d, dict)]


def ensure_namespace(client: KubeClient, namespace: str) -> None:
    from kubesift.util import check_name

    check_name(namespace, "namespace")
    try:
        ns = client.get(f"/api/v1/namespaces/{namespace}")
    except KubeError as exc:
        if exc.status != 404:
            raise
        client.request("POST", "/api/v1/namespaces", body={
            "apiVersion": "v1", "kind": "Namespace",
            "metadata": {"name": namespace, "labels": {DRILL_LABEL: "true"}}})
        return
    if ((ns.get("metadata") or {}).get("labels") or {}).get(DRILL_LABEL) != "true":
        raise ValueError(f"namespace {namespace} exists and is not a drill namespace (missing label {DRILL_LABEL}=true); "
                         "drills never touch other namespaces")


def _apply(client: KubeClient, doc: dict[str, Any], namespace: str) -> None:
    doc.setdefault("metadata", {})["namespace"] = namespace
    doc["metadata"].setdefault("labels", {})[DRILL_LABEL] = "true"
    path = _api_path(doc)
    if not path:
        raise ValueError(f"unsupported kind {doc.get('kind')}")
    client.request("PATCH", path, query={"fieldManager": "kubesift-drill", "force": "true"},
                   body=json.dumps(doc), content_type="application/apply-patch+yaml")


def start(client: KubeClient, drill_id: str, namespace: str, wait: float = 120.0) -> Drill:
    drill = DRILLS[drill_id]
    ensure_namespace(client, namespace)
    for i, file in enumerate(drill.files):
        docs = _docs(file)
        for doc in docs:
            _apply(client, doc, namespace)
        if i < len(drill.files) - 1:  # multi-step drills: let the good version roll out first
            names = [d["metadata"]["name"] for d in docs if d.get("kind") == "Deployment"]
            deadline = time.monotonic() + wait
            while names and time.monotonic() < deadline:
                d = client.get(f"/apis/apps/v1/namespaces/{namespace}/deployments/{names[0]}")
                st = d.get("status") or {}
                if st.get("readyReplicas", 0) >= (d.get("spec") or {}).get("replicas", 1):
                    break
                time.sleep(3)
    return drill


def stop(client: KubeClient, namespace: str) -> None:
    from kubesift.util import check_name

    check_name(namespace, "namespace")
    ns = client.get(f"/api/v1/namespaces/{namespace}")
    if ((ns.get("metadata") or {}).get("labels") or {}).get(DRILL_LABEL) != "true":
        raise ValueError(f"refusing to delete {namespace}: it is not a drill namespace")
    client.request("DELETE", f"/api/v1/namespaces/{namespace}")
