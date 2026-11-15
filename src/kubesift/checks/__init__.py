"""Importing this package registers every check."""

from kubesift.checks import (  # noqa: F401
    config_refs,
    events,
    hygiene,
    lifecycle,
    network,
    nodes,
    pods,
    storage,
    workloads,
)
from kubesift.checks.base import REGISTRY, Context, run_checks

__all__ = ["REGISTRY", "Context", "run_checks"]
