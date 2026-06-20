"use client";

import { useState, useEffect, useRef } from "react";
import type { ReactNode } from "react";
import { Diagnosis } from "@/types";

interface DiagnosisCardProps {
  diagnosis: Diagnosis;
}

const SEVERITY_INLINE: Record<string, React.CSSProperties> = {
  critical: { background: "#450a0a", color: "#f87171", border: "1px solid #7f1d1d" },
  high:     { background: "#431407", color: "#fb923c", border: "1px solid #7c2d12" },
  medium:   { background: "#422006", color: "#facc15", border: "1px solid #713f12" },
  low:      { background: "#052e16", color: "#4ade80", border: "1px solid #14532d" },
};

function confidenceColor(c: number) {
  return c >= 70 ? "#10b981" : c >= 40 ? "#f59e0b" : "#ef4444";
}
function confidenceTextColor(c: number) {
  return c >= 70 ? "#4ade80" : c >= 40 ? "#facc15" : "#f87171";
}

function useCountUp(target: number, duration = 1500) {
  const [val, setVal] = useState(0);
  const frameRef = useRef<number>(0);
  useEffect(() => {
    if (target <= 0) { setVal(0); return; }
    const start = performance.now();
    const tick = (now: number) => {
      const t = Math.min((now - start) / duration, 1);
      const eased = 1 - Math.pow(1 - t, 3);
      setVal(Math.round(eased * target));
      if (t < 1) frameRef.current = requestAnimationFrame(tick);
    };
    frameRef.current = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(frameRef.current);
  }, [target, duration]);
  return val;
}

function ConfidenceCircle({ value }: { value: number }) {
  const display = useCountUp(value);
  const r = 28;
  const circ = 2 * Math.PI * r;
  const filled = circ * (display / 100);
  const color = confidenceColor(value);

  return (
    <div style={{ position: "relative", width: "72px", height: "72px", flexShrink: 0 }}>
      <svg width="72" height="72" style={{ transform: "rotate(-90deg)" }} viewBox="0 0 64 64">
        <circle cx="32" cy="32" r={r} stroke="var(--bg-surface-2)" strokeWidth="7" fill="none" />
        <circle cx="32" cy="32" r={r} stroke={color} strokeWidth="7" fill="none"
          strokeDasharray={`${filled} ${circ}`} strokeLinecap="round"
          style={{ transition: "stroke-dasharray 0.05s linear" }} />
      </svg>
      <div style={{ position: "absolute", inset: 0, display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center" }}>
        <span style={{ fontSize: "16px", fontWeight: 700, lineHeight: 1, color: confidenceTextColor(value) }}>{display}</span>
        <span style={{ fontSize: "10px", color: "var(--text-muted)", lineHeight: 1, marginTop: "2px" }}>conf.</span>
      </div>
    </div>
  );
}

function SeverityBadge({ severity }: { severity: string }) {
  const style = SEVERITY_INLINE[severity] ?? SEVERITY_INLINE.medium;
  return (
    <span style={{ ...style, display: "inline-flex", alignItems: "center", padding: "3px 10px", borderRadius: "4px", fontSize: "11px", fontWeight: 700, textTransform: "uppercase", letterSpacing: "0.06em" }}>
      {severity}
    </span>
  );
}

function CopyButton({ text, label = "Copy" }: { text: string; label?: string }) {
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    await navigator.clipboard.writeText(text);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };
  return (
    <button onClick={copy}
      style={{ flexShrink: 0, padding: "4px 6px", borderRadius: "4px", background: "none", border: "1px solid var(--border)", color: copied ? "#4ade80" : "var(--text-muted)", cursor: "pointer", fontSize: "11px", display: "flex", alignItems: "center" }}
      title={label}>
      {copied ? (
        <svg width="12" height="12" fill="none" viewBox="0 0 24 24" stroke="currentColor">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2.5} d="M5 13l4 4L19 7" />
        </svg>
      ) : (
        <svg width="12" height="12" fill="none" viewBox="0 0 24 24" stroke="currentColor">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
            d="M8 16H6a2 2 0 01-2-2V6a2 2 0 012-2h8a2 2 0 012 2v2m-6 12h8a2 2 0 002-2v-8a2 2 0 00-2-2h-8a2 2 0 00-2 2v8a2 2 0 002 2z" />
        </svg>
      )}
    </button>
  );
}

function CollapsibleSection({
  title, children, defaultOpen = false,
}: {
  title: string; children: ReactNode; defaultOpen?: boolean;
}) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <div style={{ borderTop: "1px solid var(--border-subtle)", paddingTop: "16px" }}>
      <button onClick={() => setOpen((v) => !v)}
        style={{ display: "flex", alignItems: "center", justifyContent: "space-between", width: "100%", background: "none", border: "none", cursor: "pointer", padding: 0 }}>
        <h3 style={{ fontSize: "11px", fontWeight: 600, color: "var(--text-muted)", textTransform: "uppercase", letterSpacing: "0.08em", margin: 0 }}>
          {title}
        </h3>
        <svg width="14" height="14" fill="none" viewBox="0 0 24 24" stroke="currentColor"
          style={{ color: "var(--text-muted)", transform: open ? "rotate(180deg)" : "rotate(0)", transition: "transform 0.2s" }}>
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
        </svg>
      </button>
      <div style={{ overflow: "hidden", transition: "max-height 0.3s ease", maxHeight: open ? "1200px" : "0", opacity: open ? 1 : 0, marginTop: open ? "10px" : "0" }}>
        {children}
      </div>
    </div>
  );
}

function buildMarkdown(d: Diagnosis): string {
  return `# KubeOracle Diagnosis\n\n**Severity:** ${d.severity.toUpperCase()}\n**Confidence:** ${d.confidence}%\n\n## Root Cause\n${d.root_cause}\n\n## Explanation\n${d.explanation}\n\n## Recommended Fix\n${d.fix}\n\n## kubectl Commands\n${d.kubectl_commands.map((c) => `\`\`\`bash\n${c}\n\`\`\``).join("\n")}\n\n## Contributing Factors\n${d.contributing_factors.map((f) => `- ${f}`).join("\n")}\n\n## Prevention\n${d.prevention}\n`;
}

export default function DiagnosisCard({ diagnosis }: DiagnosisCardProps) {
  const [shared, setShared] = useState(false);

  const handleShare = async () => {
    await navigator.clipboard.writeText(buildMarkdown(diagnosis));
    setShared(true);
    setTimeout(() => setShared(false), 2000);
  };

  const handleExport = () => {
    const blob = new Blob([buildMarkdown(diagnosis)], { type: "text/markdown" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `kubeoracle-diagnosis-${Date.now()}.md`;
    a.click();
    URL.revokeObjectURL(url);
  };

  return (
    <div style={{ background: "var(--bg-surface)", border: "1px solid var(--border)", borderRadius: "8px", overflow: "hidden" }} className="animate-in">
      {/* Header */}
      <div style={{ padding: "14px 18px", borderBottom: "1px solid var(--border)", display: "flex", alignItems: "center", justifyContent: "space-between", gap: "12px", flexWrap: "wrap" }}>
        <div style={{ display: "flex", alignItems: "center", gap: "10px", flexWrap: "wrap" }}>
          <h2 style={{ fontSize: "13px", fontWeight: 600, color: "var(--text-primary)", margin: 0 }}>AI Diagnosis</h2>
          <SeverityBadge severity={diagnosis.severity} />
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
          <button onClick={handleShare}
            style={{ display: "flex", alignItems: "center", gap: "5px", padding: "5px 10px", borderRadius: "5px", fontSize: "12px", color: "var(--text-secondary)", background: "none", border: "1px solid var(--border)", cursor: "pointer" }}>
            {shared ? (
              <svg width="12" height="12" fill="none" viewBox="0 0 24 24" stroke="#4ade80"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2.5} d="M5 13l4 4L19 7" /></svg>
            ) : (
              <svg width="12" height="12" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M8.684 13.342C8.886 12.938 9 12.482 9 12c0-.482-.114-.938-.316-1.342m0 2.684a3 3 0 110-2.684m0 2.684l6.632 3.316m-6.632-6l6.632-3.316m0 0a3 3 0 105.367-2.684 3 3 0 00-5.367 2.684zm0 9.316a3 3 0 105.368 2.684 3 3 0 00-5.368-2.684z" /></svg>
            )}
            <span>{shared ? "Copied!" : "Share"}</span>
          </button>
          <button onClick={handleExport}
            style={{ display: "flex", alignItems: "center", gap: "5px", padding: "5px 10px", borderRadius: "5px", fontSize: "12px", color: "var(--text-secondary)", background: "none", border: "1px solid var(--border)", cursor: "pointer" }}>
            <svg width="12" height="12" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4" /></svg>
            <span>Export</span>
          </button>
          <ConfidenceCircle value={diagnosis.confidence} />
        </div>
      </div>

      {/* Body */}
      <div style={{ padding: "18px", overflowY: "auto", maxHeight: "70vh", display: "flex", flexDirection: "column", gap: "16px" }}>
        <CollapsibleSection title="Root Cause" defaultOpen>
          <p style={{ fontSize: "14px", fontWeight: 500, color: "var(--text-primary)", margin: 0, lineHeight: 1.6 }}>{diagnosis.root_cause}</p>
        </CollapsibleSection>

        {diagnosis.explanation && (
          <CollapsibleSection title="Explanation">
            <p style={{ fontSize: "13px", color: "var(--text-secondary)", margin: 0, lineHeight: 1.6 }}>{diagnosis.explanation}</p>
          </CollapsibleSection>
        )}

        {diagnosis.fix && (
          <CollapsibleSection title="Recommended Fix">
            <p style={{ fontSize: "13px", color: "var(--text-secondary)", margin: 0, lineHeight: 1.6 }}>{diagnosis.fix}</p>
          </CollapsibleSection>
        )}

        {diagnosis.kubectl_commands?.length > 0 && (
          <CollapsibleSection title={`kubectl Commands (${diagnosis.kubectl_commands.length})`} defaultOpen>
            <div style={{ display: "flex", justifyContent: "flex-end", marginBottom: "8px" }}>
              <button onClick={() => navigator.clipboard.writeText(diagnosis.kubectl_commands.join("\n"))}
                style={{ fontSize: "11px", color: "var(--text-muted)", background: "none", border: "none", cursor: "pointer", display: "flex", alignItems: "center", gap: "4px" }}>
                <svg width="11" height="11" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M8 16H6a2 2 0 01-2-2V6a2 2 0 012-2h8a2 2 0 012 2v2m-6 12h8a2 2 0 002-2v-8a2 2 0 00-2-2h-8a2 2 0 00-2 2v8a2 2 0 002 2z" /></svg>
                Copy All
              </button>
            </div>
            <div style={{ display: "flex", flexDirection: "column", gap: "8px" }}>
              {diagnosis.kubectl_commands.map((cmd, i) => (
                <div key={i} style={{ display: "flex", alignItems: "flex-start", gap: "8px", background: "#010409", border: "1px solid #1c2028", borderRadius: "6px", padding: "10px 12px" }}>
                  <span style={{ fontSize: "12px", fontFamily: "var(--font-mono, monospace)", color: "#52525b", flexShrink: 0, marginTop: "1px", userSelect: "none" }}>$</span>
                  <code style={{ flex: 1, fontSize: "13px", color: "#7ee787", fontFamily: "var(--font-mono, monospace)", wordBreak: "break-all", lineHeight: 1.5 }}>{cmd}</code>
                  <CopyButton text={cmd} label="Copy command" />
                </div>
              ))}
            </div>
          </CollapsibleSection>
        )}

        {diagnosis.contributing_factors?.length > 0 && (
          <CollapsibleSection title="Contributing Factors">
            <ul style={{ margin: 0, padding: 0, listStyle: "none", display: "flex", flexDirection: "column", gap: "6px" }}>
              {diagnosis.contributing_factors.map((f, i) => (
                <li key={i} style={{ display: "flex", alignItems: "flex-start", gap: "8px", fontSize: "13px", color: "var(--text-secondary)" }}>
                  <span style={{ color: "var(--primary)", marginTop: "2px", flexShrink: 0 }}>•</span>
                  {f}
                </li>
              ))}
            </ul>
          </CollapsibleSection>
        )}

        {diagnosis.prevention && (
          <CollapsibleSection title="Prevention">
            <p style={{ fontSize: "13px", color: "var(--text-secondary)", margin: 0, lineHeight: 1.6, fontStyle: "italic", borderLeft: "2px solid var(--border)", paddingLeft: "12px" }}>
              {diagnosis.prevention}
            </p>
          </CollapsibleSection>
        )}
      </div>
    </div>
  );
}
