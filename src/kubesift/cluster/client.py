"""Thin wrapper over the official Kubernetes Python client that returns plain JSON dicts.

Using raw API paths (instead of the typed models) keeps objects identical to
`kubectl get -o json`, so every check works the same on live data, saved
snapshots and manifest files.
"""

from __future__ import annotations

import json
import os
from typing import Any


class KubeError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(f"HTTP {status}: {message}")
        self.status = status
        self.message = message


class KubeClient:
    def __init__(self, kubeconfig: str | None = None, context: str | None = None, timeout: int = 20):
        from kubernetes import client, config

        self.timeout = timeout
        if not kubeconfig and not context and os.environ.get("KUBERNETES_SERVICE_HOST"):
            config.load_incluster_config()
            cfg = client.Configuration.get_default_copy()
            self.context_name = "in-cluster"
        else:
            cfg = client.Configuration()
            config.load_kube_config(config_file=kubeconfig, context=context, client_configuration=cfg)
            if context:
                self.context_name = context
            else:
                _, active = config.list_kube_config_contexts(config_file=kubeconfig)
                self.context_name = (active or {}).get("name", "default")
        self.api = client.ApiClient(cfg)

    def request(
        self,
        method: str,
        path: str,
        query: dict[str, Any] | None = None,
        body: Any = None,
        content_type: str | None = None,
        raw: bool = False,
    ) -> Any:
        from kubernetes.client.exceptions import ApiException

        headers = {"Accept": "*/*" if raw else "application/json"}
        if content_type:
            headers["Content-Type"] = content_type
        query_params = [(k, v) for k, v in (query or {}).items() if v is not None]
        try:
            if hasattr(self.api, "param_serialize"):  # kubernetes >= 37 (new generator)
                params = self.api.param_serialize(
                    method=method, resource_path=path, query_params=query_params,
                    header_params=headers, body=body, auth_settings=["BearerToken"],
                )
                resp = self.api.call_api(*params, _request_timeout=self.timeout)
                data = resp.read()
                status = resp.status
            else:  # kubernetes <= 36
                resp = self.api.call_api(
                    path, method, query_params=query_params, header_params=headers, body=body,
                    auth_settings=["BearerToken"], _preload_content=False, _return_http_data_only=True,
                    _request_timeout=self.timeout,
                )
                data = resp.data
                status = resp.status
        except ApiException as exc:
            raise KubeError(exc.status or 0, _message(exc.body) or str(exc.reason)) from exc
        if status >= 400:
            raise KubeError(status, _message(data))
        text = data.decode("utf-8", "replace") if isinstance(data, bytes) else (data or "")
        if raw:
            return text
        return json.loads(text) if text else {}

    def get(self, path: str, **query: Any) -> Any:
        return self.request("GET", path, query=query)

    def list_all(self, path: str, max_items: int = 5000, page: int = 500) -> tuple[list[dict], bool]:
        """List a collection with pagination. Returns (items, truncated)."""
        items: list[dict] = []
        token: str | None = None
        while True:
            data = self.get(path, limit=page, **({"continue": token} if token else {}))
            items.extend(data.get("items") or [])
            token = (data.get("metadata") or {}).get("continue")
            if not token:
                return items, False
            if len(items) >= max_items:
                return items[:max_items], True

    def pod_logs(self, namespace: str, pod: str, container: str, previous: bool, tail: int = 200) -> str:
        return self.request(
            "GET",
            f"/api/v1/namespaces/{namespace}/pods/{pod}/log",
            query={"container": container, "previous": "true" if previous else None, "tailLines": tail},
            raw=True,
        )

    def server_version(self) -> str | None:
        try:
            return self.get("/version").get("gitVersion")
        except Exception:
            return None


def _message(body: Any) -> str:
    if isinstance(body, bytes):
        body = body.decode("utf-8", "replace")
    if isinstance(body, str):
        try:
            return json.loads(body).get("message", body)
        except (ValueError, AttributeError):
            return body[:300]
    return str(body)[:300]


def list_contexts(kubeconfig: str | None = None) -> tuple[list[dict[str, str]], str | None]:
    """Return (contexts, current) from the kubeconfig. Each context: name, cluster, namespace."""
    from kubernetes import config

    if not kubeconfig and os.environ.get("KUBERNETES_SERVICE_HOST") and not os.path.exists(
        os.path.expanduser("~/.kube/config")
    ):
        return [{"name": "in-cluster", "cluster": "in-cluster", "namespace": ""}], "in-cluster"
    contexts, active = config.list_kube_config_contexts(config_file=kubeconfig)
    out = [
        {
            "name": c["name"],
            "cluster": (c.get("context") or {}).get("cluster", ""),
            "namespace": (c.get("context") or {}).get("namespace", ""),
        }
        for c in contexts or []
    ]
    return out, (active or {}).get("name")
