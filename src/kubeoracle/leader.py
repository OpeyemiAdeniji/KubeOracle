"""Leader election with a coordination.k8s.io Lease, the same mechanism Kubernetes controllers use.

When several replicas run, only the Lease holder does background scans and sends notifications.
If it dies, another replica takes over after the lease expires (default 30s).
"""

from __future__ import annotations

import logging
import os
import socket
import threading
from datetime import datetime, timedelta

from kubeoracle.cluster.client import KubeClient, KubeError
from kubeoracle.util import UTC, now

log = logging.getLogger("kubeoracle.leader")
_IN_CLUSTER_NS = "/var/run/secrets/kubernetes.io/serviceaccount/namespace"


def _micro(t: datetime) -> str:
    return t.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _parse(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def pod_namespace() -> str:
    if os.environ.get("POD_NAMESPACE"):
        return os.environ["POD_NAMESPACE"]
    try:
        with open(_IN_CLUSTER_NS, encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return "default"


class LeaderElector:
    def __init__(self, client: KubeClient, namespace: str, name: str = "kubeoracle-scanner",
                 identity: str | None = None, lease_seconds: int = 30):
        self.client = client
        self.namespace = namespace
        self.name = name
        self.identity = identity or os.environ.get("POD_NAME") or socket.gethostname()
        self.lease_seconds = lease_seconds
        self.is_leader = False
        self._stop = threading.Event()

    @property
    def _path(self) -> str:
        return f"/apis/coordination.k8s.io/v1/namespaces/{self.namespace}/leases"

    def try_acquire(self) -> bool:
        """One round: take the lease if free or expired, renew it if ours. Returns leadership."""
        t = now()
        try:
            lease = self.client.get(f"{self._path}/{self.name}")
        except KubeError as exc:
            if exc.status != 404:
                raise
            body = {"apiVersion": "coordination.k8s.io/v1", "kind": "Lease",
                    "metadata": {"name": self.name, "namespace": self.namespace},
                    "spec": {"holderIdentity": self.identity, "leaseDurationSeconds": self.lease_seconds,
                             "acquireTime": _micro(t), "renewTime": _micro(t), "leaseTransitions": 0}}
            try:
                self.client.request("POST", self._path, body=body)
                return True
            except KubeError as create_exc:
                if create_exc.status == 409:  # another replica created it first
                    return False
                raise
        spec = lease.get("spec") or {}
        holder = spec.get("holderIdentity")
        renewed = _parse(spec.get("renewTime")) or _parse(spec.get("acquireTime"))
        duration = spec.get("leaseDurationSeconds") or self.lease_seconds
        expired = renewed is None or t > renewed + timedelta(seconds=duration)
        if holder != self.identity and not expired:
            return False
        if holder != self.identity:
            spec["acquireTime"] = _micro(t)
            spec["leaseTransitions"] = (spec.get("leaseTransitions") or 0) + 1
            log.info("acquiring leadership from %s", holder or "nobody")
        spec.update(holderIdentity=self.identity, renewTime=_micro(t), leaseDurationSeconds=self.lease_seconds)
        lease["spec"] = spec
        try:
            # PUT with the resourceVersion we read: a concurrent update makes this fail with 409.
            self.client.request("PUT", f"{self._path}/{self.name}", body=lease)
            return True
        except KubeError as exc:
            if exc.status == 409:
                return False
            raise

    def run(self) -> None:
        interval = max(self.lease_seconds / 3, 2)
        while not self._stop.is_set():
            try:
                leader = self.try_acquire()
            except Exception as exc:  # API unreachable: step down, keep trying
                log.warning("leader election failed: %s", exc)
                leader = False
            if leader != self.is_leader:
                log.info("%s is %s the leader", self.identity, "now" if leader else "no longer")
            self.is_leader = leader
            self._stop.wait(interval)

    def start(self) -> LeaderElector:
        threading.Thread(target=self.run, daemon=True, name="leader-election").start()
        return self

    def stop(self) -> None:
        self._stop.set()


class AlwaysLeader:
    is_leader = True

    def stop(self) -> None:
        pass
