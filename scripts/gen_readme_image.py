"""Render the README's terminal image from a real diagnosis of the demo cluster: python scripts/gen_readme_image.py

The image is real KubeSift output (rules only, no LLM) for the `shop` namespace of tests/fixtures.broken_cluster,
so it stays honest when the output format changes: rerun this script.
"""

from __future__ import annotations

import sys
from pathlib import Path

from rich.console import Console
from rich.terminal_theme import MONOKAI
from rich.text import Text

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests"))

from fixtures import broken_cluster  # noqa: E402

from kubesift.analysis import engine  # noqa: E402
from kubesift.config import load_settings  # noqa: E402
from kubesift.report import diagnosis_panel  # noqa: E402


def main() -> None:
    settings = load_settings(llm_provider="none")
    snap = broken_cluster()
    snap.namespace = "shop"
    report = engine.investigate(settings, snap, use_llm=False)
    console = Console(record=True, width=96, force_terminal=True, color_system="truecolor")
    console.print(Text.assemble(("$ ", "bold cyan"), "kubectl sift diagnose -n shop"))
    console.print(diagnosis_panel(report.diagnosis, max_causes=2))
    counts = report.counts()
    console.print(Text(f"… {len(report.diagnosis.root_causes) - 2} more causes and a table of every problem", style="dim"))
    console.print(" ".join(f"{k}={counts[k]}" for k in ("critical", "high", "medium", "low")))
    out = ROOT / "docs" / "images" / "diagnose.svg"
    out.parent.mkdir(parents=True, exist_ok=True)
    console.save_svg(str(out), title="kubectl sift diagnose", theme=MONOKAI)
    print(f"wrote {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
