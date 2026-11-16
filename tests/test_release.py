"""Release metadata stays consistent: package version, chart version, appVersion and the Artifact Hub image tag."""

import json
from pathlib import Path

import pytest
import yaml

from kubesift import __version__

ROOT = Path(__file__).resolve().parent.parent


def test_chart_versions_match_the_package():
    chart = yaml.safe_load((ROOT / "deploy" / "helm" / "kubesift" / "Chart.yaml").read_text(encoding="utf-8"))
    assert chart["version"] == __version__
    assert chart["appVersion"] == __version__
    images = yaml.safe_load(chart["annotations"]["artifacthub.io/images"])
    assert [i["image"] for i in images] == [f"ghcr.io/opeyemiadeniji/kubesift:{__version__}"]
    assert yaml.safe_load(chart["annotations"]["artifacthub.io/links"])  # valid YAML inside the annotation
    changes = yaml.safe_load(chart["annotations"]["artifacthub.io/changes"])
    assert changes and all(c["kind"] in {"added", "changed", "deprecated", "removed", "fixed", "security"}
                           and c["description"] for c in changes)


def test_values_schema_accepts_the_defaults_and_catches_typos():
    jsonschema = pytest.importorskip("jsonschema")
    chart = ROOT / "deploy" / "helm" / "kubesift"
    schema = json.loads((chart / "values.schema.json").read_text(encoding="utf-8"))
    values = yaml.safe_load((chart / "values.yaml").read_text(encoding="utf-8"))
    jsonschema.validate(values, schema)
    # what CI and the docs set
    jsonschema.validate(dict(values, replicaCount=2, database={"url": "postgresql://u:p@db/k"},
                             llm=dict(values["llm"], provider="anthropic", timeout=600)), schema)
    for bad in ({"replicaCounts": 2}, {"llm": {"provider": "gemini"}}, {"scan": {"notifyMinSeverity": "urgent"}},
                {"replicaCount": 0}):
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(dict(values, **bad), schema)
