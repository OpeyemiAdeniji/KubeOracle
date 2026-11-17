"""Draw the architecture diagram in light and dark versions (README picks one with <picture>).

    python scripts/gen_architecture_svg.py   # -> docs/images/architecture-{light,dark}.svg
"""

from __future__ import annotations

from html import escape
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "docs" / "images"
W, H = 1200, 590

THEMES = {
    "dark": {"bg": "#0d1117", "panel": "#161b22", "panel2": "#1c2230", "line": "#30363d", "text": "#e6edf3",
             "muted": "#8b949e", "teal": "#2dd4bf", "indigo": "#818cf8", "amber": "#fbbf24", "arrow": "#6e7681"},
    "light": {"bg": "#ffffff", "panel": "#f6f8fa", "panel2": "#eef1f8", "line": "#d0d7de", "text": "#1f2328",
              "muted": "#59636e", "teal": "#0d9488", "indigo": "#4f46e5", "amber": "#b45309", "arrow": "#8c959f"},
}

USE = ["CLI · kubectl sift", "Web dashboard + API", "AI assistants (MCP)", "CI: GitHub Action, pre-commit",
       "Alertmanager → Slack, Teams"]
GET = [("Incidents with evidence", "symptoms grouped, cause first"),
       ("The change that caused it", "rollout, config edit, Helm/Argo sync"),
       ("The fix and exact commands", "kubectl, rollback, dry run first"),
       ("Reports", "Slack, SARIF, Markdown, postmortem")]
# (column, row, title, subtitle); columns at x = 320/520/720, rows at y = 112/252
STAGES = [(0, 1, "Collect", "API, logs, metrics"), (0, 0, "Snapshot", "redacted, saveable"),
          (1, 0, "72 checks", "deterministic findings"), (1, 1, "Changes", "rollouts, edits, syncs"),
          (2, 0, "Correlate", "incident + likely trigger"), (2, 1, "Explain", "rules, or AI checked")]


def box(c, x, y, w, h, title, sub=None, accent=None, dashed=False):
    stroke = c[accent] if accent else c["line"]
    dash = ' stroke-dasharray="6 5"' if dashed else ""
    out = (f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="12" fill="{c["panel"]}" stroke="{stroke}"'
           f' stroke-width="{1.6 if accent else 1.2}"{dash}/>')
    ty = y + (h / 2 + 5 if sub is None else h / 2 - 4)
    out += f'<text x="{x + w / 2}" y="{ty}" class="t">{escape(title)}</text>'
    if sub:
        out += f'<text x="{x + w / 2}" y="{ty + 21}" class="s">{escape(sub)}</text>'
    return out


def arrow(x1, y1, x2, y2, dashed=False):
    dash = ' stroke-dasharray="5 5"' if dashed else ""
    return f'<path d="M{x1} {y1} L{x2} {y2}" class="a"{dash} marker-end="url(#head)"/>'


def svg(theme: str) -> str:
    c = THEMES[theme]
    p = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}" role="img" '
         f'aria-labelledby="t d">',
         '<title id="t">KubeSift architecture</title>',
         '<desc id="d">KubeSift reads the cluster, runs checks and change detection on a snapshot, correlates '
         'problems with the change that caused them and explains the fix. It is used from the CLI, a dashboard, '
         'AI assistants, CI and alerting. AI is optional.</desc>',
         f'<defs><marker id="head" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" '
         f'orient="auto-start-reverse"><path d="M0 0 10 5 0 10Z" fill="{c["arrow"]}"/></marker></defs>',
         f'<style>.t{{font:600 15px system-ui,-apple-system,"Segoe UI",sans-serif;fill:{c["text"]};text-anchor:middle}}'
         f'.s{{font:13px system-ui,-apple-system,"Segoe UI",sans-serif;fill:{c["muted"]};text-anchor:middle}}'
         f'.h{{font:700 12px system-ui,-apple-system,"Segoe UI",sans-serif;fill:{c["muted"]};letter-spacing:.12em}}'
         f'.a{{stroke:{c["arrow"]};stroke-width:1.8;fill:none}}</style>',
         f'<rect width="{W}" height="{H}" rx="16" fill="{c["bg"]}"/>']

    # Left: where it is used
    p.append('<text x="32" y="48" class="h">USE IT FROM</text>')
    for i, label in enumerate(USE):
        p.append(box(c, 32, 66 + i * 70, 232, 54, label))
    p.append(arrow(268, 237, 296, 237))

    # Middle: the engine
    p.append(f'<rect x="300" y="66" width="600" height="300" rx="16" fill="{c["panel2"]}" stroke="{c["line"]}"/>')
    p.append(f'<text x="320" y="96" class="h" style="fill:{c["teal"]}">KUBESIFT</text>')
    xs, ys, bw, bh = (320, 520, 720), (112, 252), 160, 84
    for col, row, title, sub in STAGES:
        accent = "amber" if title == "Correlate" else ("teal" if title == "72 checks" else None)
        p.append(box(c, xs[col], ys[row], bw, bh, title, sub, accent))
    p += [arrow(400, 250, 400, 202),                 # collect -> snapshot
          arrow(482, 154, 516, 154),                 # snapshot -> checks
          arrow(482, 184, 518, 252),                 # snapshot -> changes
          arrow(682, 154, 716, 154),                 # checks -> correlate
          arrow(682, 280, 718, 198),                 # changes -> correlate
          arrow(800, 198, 800, 248),                 # correlate -> explain
          arrow(882, 294, 924, 294)]                 # explain -> results

    # Right: what you get
    p.append('<text x="928" y="48" class="h">YOU GET</text>')
    for i, (title, sub) in enumerate(GET):
        p.append(box(c, 928, 66 + i * 76, 244, 62, title, sub))

    # Bottom: the cluster and the optional AI
    p.append(box(c, 300, 404, 280, 76, "Your cluster", "Kubernetes API · pod logs · metrics", "teal"))
    p.append(arrow(400, 402, 400, 338))
    p.append(box(c, 620, 404, 280, 76, "Optional AI, your own key", "Ollama · Anthropic · OpenAI-compatible",
                 "indigo", dashed=True))
    p.append(arrow(800, 402, 800, 338, dashed=True))

    p.append(f'<text x="{W / 2}" y="540" class="s">Runs on your machine or in your cluster. Read-only by default. '
             'Nothing leaves it unless you turn on AI, and then only redacted evidence.</text>')
    p.append(f'<text x="{W / 2}" y="562" class="s">AI answers are checked against the findings they cite; '
             'without AI, the rule-based diagnosis gives the same incidents and fixes.</text>')
    p.append("</svg>")
    return "\n".join(p) + "\n"


if __name__ == "__main__":
    for theme in THEMES:
        path = OUT / f"architecture-{theme}.svg"
        path.write_text(svg(theme), encoding="utf-8", newline="\n")
        print(path)
