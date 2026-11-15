"""Regenerate docs/checks.md from the check registry: python scripts/gen_check_docs.py"""

from pathlib import Path

from kubeoracle.checks import REGISTRY

ORDER = ["workload", "config", "network", "platform", "node", "scheduling", "storage", "resources",
         "reliability", "security", "upgrade", "cost"]

lines = ["# Checks", "",
         f"KubeOracle runs {len(REGISTRY)} checks. **Problems** are broken now; **risks** will hurt later. "
         "Checks marked *lint* also run on manifest files (`kubeoracle lint`), without a cluster.", "",
         "Regenerate this page with `python scripts/gen_check_docs.py`.", ""]
for cat in ORDER:
    items = sorted((c for c in REGISTRY.values() if c.category.value == cat), key=lambda c: c.id)
    if not items:
        continue
    lines += [f"## {cat.title()}", ""]
    for c in items:
        tags = [c.severity.value, "risk" if c.risk else "problem"] + ([] if c.live_only else ["lint"])
        lines += [f"### {c.id}", "", f"**{c.title}** · {' · '.join(tags)}", "", c.description, ""]
Path(__file__).parent.parent.joinpath("docs", "checks.md").write_text("\n".join(lines), encoding="utf-8")
print(f"wrote {len(REGISTRY)} checks")
