"""Small helpers: Kubernetes time stamps, resource quantities, label selectors."""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any

UTC = timezone.utc


def now() -> datetime:
    return datetime.now(UTC)


def parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def age(value: str | None) -> timedelta | None:
    t = parse_time(value)
    return now() - t if t else None


def human_duration(delta: timedelta | None) -> str:
    if delta is None:
        return "unknown"
    s = int(delta.total_seconds())
    if s < 0:
        s = 0
    if s < 60:
        return f"{s}s"
    if s < 3600:
        return f"{s // 60}m"
    if s < 86400:
        return f"{s // 3600}h{(s % 3600) // 60:02d}m"
    return f"{s // 86400}d{(s % 86400) // 3600}h"


_DURATION = re.compile(r"^(\d+)([smhdw])$")


def parse_duration(text: str) -> timedelta:
    m = _DURATION.match(text.strip())
    if not m:
        raise ValueError(f"invalid duration {text!r}; use e.g. 30m, 2h, 1d")
    n, unit = int(m.group(1)), m.group(2)
    return {"s": timedelta(seconds=n), "m": timedelta(minutes=n), "h": timedelta(hours=n),
            "d": timedelta(days=n), "w": timedelta(weeks=n)}[unit]


_SUFFIX = {
    "Ki": 2**10, "Mi": 2**20, "Gi": 2**30, "Ti": 2**40, "Pi": 2**50, "Ei": 2**60,
    "n": 1e-9, "u": 1e-6, "m": 1e-3, "k": 1e3, "K": 1e3, "M": 1e6, "G": 1e9, "T": 1e12, "P": 1e15, "E": 1e18,
}
_QTY = re.compile(r"^([+-]?[0-9.]+(?:[eE][+-]?\d+)?)([a-zA-Z]{0,2})$")


def parse_quantity(value: Any) -> float:
    """Convert a Kubernetes quantity ('250m', '1Gi', 2) to a float in base units."""
    if value is None or value == "":
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    m = _QTY.match(str(value).strip())
    if not m:
        return 0.0
    number, suffix = float(m.group(1)), m.group(2)
    return number * _SUFFIX.get(suffix, 1.0)


def fmt_cpu(cores: float) -> str:
    return f"{cores * 1000:.0f}m" if cores < 1 else f"{cores:.2f}".rstrip("0").rstrip(".")


def fmt_bytes(n: float) -> str:
    for unit, size in (("Gi", 2**30), ("Mi", 2**20), ("Ki", 2**10)):
        if n >= size:
            return f"{n / size:.0f}{unit}" if n / size >= 10 else f"{n / size:.1f}{unit}"
    return f"{n:.0f}"


def selector_matches(selector: dict[str, str] | None, labels: dict[str, str] | None) -> bool:
    if not selector:
        return False
    labels = labels or {}
    return all(labels.get(k) == v for k, v in selector.items())


def label_selector_matches(selector: dict[str, Any] | None, labels: dict[str, str] | None) -> bool:
    """Full LabelSelector (matchLabels + matchExpressions). Empty selector matches everything."""
    if selector is None:
        return False
    labels = labels or {}
    for k, v in (selector.get("matchLabels") or {}).items():
        if labels.get(k) != v:
            return False
    for expr in selector.get("matchExpressions") or []:
        key, op, values = expr.get("key"), expr.get("operator"), expr.get("values") or []
        if op == "In" and labels.get(key) not in values:
            return False
        if op == "NotIn" and labels.get(key) in values:
            return False
        if op == "Exists" and key not in labels:
            return False
        if op == "DoesNotExist" and key in labels:
            return False
    return True


_DNS_NAME = re.compile(r"^[a-z0-9]([-a-z0-9.]{0,251}[a-z0-9])?$")


def check_name(value: str | None, what: str = "name") -> str | None:
    """Reject anything that is not a valid Kubernetes object/namespace name before it goes into an API path."""
    if value is None or value == "":
        return value
    if not _DNS_NAME.match(value) or ".." in value:
        raise ValueError(f"invalid {what} {value!r}: must be a lowercase DNS name (a-z, 0-9, '-', '.')")
    return value


def meta(obj: dict[str, Any]) -> dict[str, Any]:
    return obj.get("metadata") or {}


def name_of(obj: dict[str, Any]) -> str:
    return meta(obj).get("name", "")


def ns_of(obj: dict[str, Any]) -> str | None:
    return meta(obj).get("namespace")


def pod_spec_of(obj: dict[str, Any]) -> dict[str, Any] | None:
    """Return the pod spec of a Pod or of any workload template."""
    kind = obj.get("kind")
    spec = obj.get("spec") or {}
    if kind == "Pod":
        return spec
    if kind == "CronJob":
        return (((spec.get("jobTemplate") or {}).get("spec") or {}).get("template") or {}).get("spec")
    template = spec.get("template")
    if isinstance(template, dict):
        return template.get("spec")
    return None


def containers_of(pod_spec: dict[str, Any] | None, include_init: bool = False) -> list[dict[str, Any]]:
    if not pod_spec:
        return []
    out = list(pod_spec.get("containers") or [])
    if include_init:
        out = list(pod_spec.get("initContainers") or []) + out
    return out


def image_tag(image: str) -> str | None:
    if "@" in image:
        return "digest"
    last = image.rsplit("/", 1)[-1]
    return last.split(":", 1)[1] if ":" in last else None


def image_registry(image: str) -> str:
    first = image.split("/", 1)[0]
    if "/" in image and ("." in first or ":" in first or first == "localhost"):
        return first
    return "docker.io"
