"""Release metadata stays consistent: package version, chart version, appVersion and the Artifact Hub image tag."""

from pathlib import Path

import yaml

from kubesift import __version__

ROOT = Path(__file__).resolve().parent.parent


def test_chart_versions_match_the_package():
    chart = yaml.safe_load((ROOT / "deploy" / "helm" / "kubesift" / "Chart.yaml").read_text(encoding="utf-8"))
    assert chart["version"] == __version__
    assert chart["appVersion"] == __version__
    images = yaml.safe_load(chart["annotations"]["artifacthub.io/images"])
    assert [i["image"] for i in images] == [f"ghcr.io/opeyemiadeniji/kubesift:{__version__}"]
    for key in ("artifacthub.io/links",):
        assert yaml.safe_load(chart["annotations"][key])  # valid YAML inside the annotation
