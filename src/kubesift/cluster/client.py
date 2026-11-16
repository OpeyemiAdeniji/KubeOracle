"""Thin wrapper over the official Kubernetes Python client that returns plain JSON dicts.

Using raw API paths (instead of the typed models) keeps objects identical to
`kubectl get -o json`, so every check works the same on live data, saved
snapshots and manifest files.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any


class KubeError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(f"HTTP {status}: {message}")
        self.status = status
        self.message = message


class ClusterAccessError(Exception):
    """The cluster cannot be used at all: unreachable, or the login was rejected."""


_LOGIN_HINT = ("log in again (gcloud auth login / az login / aws sso login, or refresh the kubeconfig), "
               "or pick another context: kubectl config get-contexts, then --context NAME")
CONNECT_HINT = ("connect a cluster first, for example: gcloud container clusters get-credentials NAME --region REGION, "
                 "az aks get-credentials -g GROUP -n NAME, aws eks update-kubeconfig --name NAME, or kind create cluster; "
                 "or point KUBECONFIG / --kubeconfig at another file")


def _read_kubeconfig(kubeconfig: str | None) -> tuple[str, list[dict[str, Any]], str]:
    """Read the kubeconfig file(s) as plain YAML: (where, contexts, current-context).

    Raises FileNotFoundError when no file exists; YAML errors propagate.
    """
    import yaml

    raw = [kubeconfig] if kubeconfig else os.environ.get("KUBECONFIG", "~/.kube/config").split(os.pathsep)
    paths = [os.path.expanduser(p) for p in raw if p]
    where = ", ".join(paths)
    existing = [p for p in paths if os.path.isfile(p)]
    if not existing:
        raise FileNotFoundError(where)
    contexts: list[dict[str, Any]] = []
    current = ""
    for p in existing:
        with open(p, encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
        contexts += [c for c in data.get("contexts") or [] if isinstance(c, dict) and c.get("name")]
        current = current or data.get("current-context") or ""
    return where, contexts, current


def _explain_load_error(exc: Exception, kubeconfig: str | None, context: str | None) -> str:
    """Turn the Kubernetes client's terse kubeconfig errors into what is wrong and how to fix it."""
    try:
        where, contexts, current = _read_kubeconfig(kubeconfig)
    except FileNotFoundError as missing:
        return f"no kubeconfig found at {missing}. Fix: {CONNECT_HINT}"
    except Exception:
        return f"cannot load kubeconfig: {exc}"
    names = [c["name"] for c in contexts]
    if not names:
        return f"the kubeconfig at {where} has no clusters set up. Fix: {CONNECT_HINT}"
    available = ", ".join(names[:10])
    if context and context not in names:
        return f"context '{context}' is not in the kubeconfig at {where}. Available: {available}"
    if not context and current not in names:
        return (f"no current context is set in {where}. Pick one with --context NAME or "
                f"kubectl config use-context NAME. Available: {available}")
    return f"cannot load kubeconfig{f' context {context}' if context else ''}: {exc}"


class _LoginFailures(logging.Handler):
    def __init__(self) -> None:
        super().__init__(logging.ERROR)
        self.messages: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        msg = record.getMessage()
        if msg.startswith("exec:"):
            self.messages.append(msg.removeprefix("exec:").strip())


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
            failures = _LoginFailures()
            logging.getLogger().addHandler(failures)  # the client only *logs* a failing exec login helper
            try:
                config.load_kube_config(config_file=kubeconfig, context=context, client_configuration=cfg)
            except Exception as exc:  # missing or empty file, unknown or unset context
                raise ClusterAccessError(_explain_load_error(exc, kubeconfig, context)) from exc
            finally:
                logging.getLogger().removeHandler(failures)
            if context:
                self.context_name = context
            else:
                _, active = config.list_kube_config_contexts(config_file=kubeconfig)
                self.context_name = (active or {}).get("name", "default")
            if failures.messages:
                raise ClusterAccessError(f"the login command for context '{self.context_name}' failed: "
                                         f"{failures.messages[-1][:300]}. Fix: {_LOGIN_HINT}")
        cfg.retries = 1  # one retry for a network blip; urllib3's default (3) multiplies every timeout
        self.server = cfg.host
        self.api = client.ApiClient(cfg)

    def check_connection(self, namespace: str | None = None) -> str | None:
        """Fail fast, with a useful message, when the cluster cannot be used at all.

        Without this an unreachable cluster or a dead login made every list call fail separately, which was
        slow and ended in an empty report that looked like a healthy cluster.
        """
        where = f"context '{self.context_name}' ({self.server})"
        try:
            version = (self.request("GET", "/version", timeout=(5, 15)) or {}).get("gitVersion")
        except KubeError as exc:
            if exc.status != 401:
                raise ClusterAccessError(f"the API server for {where} answered HTTP {exc.status}: {exc.message}") from exc
            version = None  # /version needs a login on this cluster; the next call tells us if ours works
        except Exception as exc:
            raise ClusterAccessError(
                f"cannot reach the API server for {where}: {_short(exc)}. Check your network or VPN and that the "
                "cluster still exists, or pick another context: kubectl config get-contexts, then --context NAME"
            ) from exc
        probe = f"/api/v1/namespaces/{namespace}" if namespace else "/api/v1/namespaces"
        try:
            self.request("GET", probe, query={"limit": 1} if not namespace else None, timeout=(5, 15))
        except KubeError as exc:
            if exc.status == 401:
                raise ClusterAccessError(f"the cluster rejected the login for {where} (expired, revoked or deleted "
                                         f"account): {_LOGIN_HINT}") from exc
            # 403/404: logged in but limited; the checks report what they could not read
        except Exception as exc:
            raise ClusterAccessError(f"cannot reach the API server for {where}: {_short(exc)}") from exc
        return version

    def request(
        self,
        method: str,
        path: str,
        query: dict[str, Any] | None = None,
        body: Any = None,
        content_type: str | None = None,
        raw: bool = False,
        timeout: Any = None,
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
                resp = self.api.call_api(*params, _request_timeout=timeout or self.timeout)
                data = resp.read()
                status = resp.status
            else:  # kubernetes <= 36
                resp = self.api.call_api(
                    path, method, query_params=query_params, header_params=headers, body=body,
                    auth_settings=["BearerToken"], _preload_content=False, _return_http_data_only=True,
                    _request_timeout=timeout or self.timeout,
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


def _short(exc: Exception) -> str:
    """The useful tail of a urllib3 error ('Connection refused', 'timed out', 'Name or service not known')."""
    text = str(exc)
    if "actively refused" in text:  # Windows wording
        return "connection refused"
    for marker in ("Connection refused", "timed out", "Name or service not known",
                   "getaddrinfo failed", "No route to host", "certificate verify failed", "Network is unreachable"):
        if marker in text:
            return marker
    return text[:200]


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
    try:
        contexts, active = config.list_kube_config_contexts(config_file=kubeconfig)
    except Exception as exc:
        # The client refuses a kubeconfig with no contexts or no current context; both are fine to list.
        try:
            _, contexts, current = _read_kubeconfig(kubeconfig)
        except Exception:
            raise ClusterAccessError(_explain_load_error(exc, kubeconfig, None)) from exc
        active = next((c for c in contexts if c["name"] == current), None)
    out = [
        {
            "name": c["name"],
            "cluster": (c.get("context") or {}).get("cluster", ""),
            "namespace": (c.get("context") or {}).get("namespace", ""),
        }
        for c in contexts or []
    ]
    return out, (active or {}).get("name")
