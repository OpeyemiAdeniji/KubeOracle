"use client";

import { useState } from "react";
import { QueryResult } from "@/types";

const SEVERITY_INLINE: Record<string, React.CSSProperties> = {
  critical: { background: "#450a0a", color: "#f87171", border: "1px solid #7f1d1d" },
  high:     { background: "#431407", color: "#fb923c", border: "1px solid #7c2d12" },
  medium:   { background: "#422006", color: "#facc15", border: "1px solid #713f12" },
  low:      { background: "#052e16", color: "#4ade80", border: "1px solid #14532d" },
};

const DOMAIN_LABELS: Record<string, string> = {
  pods: "Pods", logs: "Logs", events: "Events", deployments: "Deployments",
  network: "Network", nodes: "Nodes", resources: "Resources",
  config_secrets: "Config & Secrets", probes: "Probes", rbac: "RBAC", scheduling: "Scheduling",
};

interface Props {
  result: QueryResult;
  context?: string;
  onSave?: (result: QueryResult, context: string) => Promise<void>;
  initialSaved?: boolean;
}

function CopyButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);
  const copy = () => {
    navigator.clipboard.writeText(text).catch(() => {});
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };
  return (
    <button onClick={copy}
      style={{ flexShrink: 0, padding: "3px 8px", borderRadius: "4px", background: "none", border: "1px solid var(--border)", color: copied ? "#4ade80" : "var(--text-muted)", cursor: "pointer", fontSize: "11px" }}>
      {copied ? "✓" : "copy"}
    </button>
  );
}

// Minimal fixed toast — appears at bottom-center, auto-dismisses
function Toast({ visible, message, variant = "success" }: { visible: boolean; message: string; variant?: "success" | "error" }) {
  return (
    <div style={{
      position: "fixed", bottom: 28, left: "50%",
      transform: `translateX(-50%) translateY(${visible ? 0 : 12}px)`,
      background: "var(--bg-surface-2)",
      border: `1px solid ${variant === "error" ? "rgba(239,68,68,0.35)" : "var(--border-subtle)"}`,
      borderRadius: 7, padding: "9px 18px",
      fontSize: 13, color: "var(--text-primary)",
      boxShadow: "0 8px 28px rgba(0,0,0,0.55)",
      opacity: visible ? 1 : 0,
      transition: "opacity 0.2s ease, transform 0.2s ease",
      zIndex: 9999, pointerEvents: "none",
      display: "flex", alignItems: "center", gap: 8,
      maxWidth: "min(480px, 90vw)",
    }}>
      {variant === "error" ? (
        <svg width="14" height="14" fill="none" viewBox="0 0 24 24" stroke="#f87171" style={{ flexShrink: 0 }}>
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2.5} d="M6 18L18 6M6 6l12 12" />
        </svg>
      ) : (
        <svg width="14" height="14" fill="none" viewBox="0 0 24 24" stroke="#4ade80" style={{ flexShrink: 0 }}>
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2.5} d="M5 13l4 4L19 7" />
        </svg>
      )}
      <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{message}</span>
    </div>
  );
}

function BookmarkIcon({ filled }: { filled: boolean }) {
  return filled ? (
    <svg width="15" height="15" viewBox="0 0 24 24" fill="currentColor">
      <path d="M17 3H7a2 2 0 00-2 2v16l7-3 7 3V5a2 2 0 00-2-2z" />
    </svg>
  ) : (
    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M17 3H7a2 2 0 00-2 2v16l7-3 7 3V5a2 2 0 00-2-2z" />
    </svg>
  );
}

export default function QueryResultCard({ result, context = "", onSave, initialSaved = false }: Props) {
  const severityKey = result.severity ?? "low";
  const severityStyle = SEVERITY_INLINE[severityKey] ?? SEVERITY_INLINE.low;
  const domains = result.investigation?.domains_checked ?? [];
  const commands = result.kubectl_commands ?? [];

  const [saveState, setSaveState] = useState<"idle" | "saving" | "saved">(initialSaved ? "saved" : "idle");
  const [toast, setToast] = useState(false);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

  const handleSave = async () => {
    if (!onSave || saveState !== "idle") return;
    setSaveState("saving");
    setErrorMsg(null);
    try {
      await onSave(result, context);
      setSaveState("saved");
      setToast(true);
      setTimeout(() => setToast(false), 3000);
    } catch (err) {
      setSaveState("idle");
      const msg = err instanceof Error ? err.message : "Save failed — check backend logs";
      console.error("[QueryResultCard] Save failed:", err);
      setErrorMsg(msg);
      setTimeout(() => setErrorMsg(null), 5000);
    }
  };

  return (
    <>
      <Toast visible={toast} message="Query saved to history" variant="success" />
      <Toast visible={!!errorMsg} message={errorMsg ?? ""} variant="error" />

      <div style={{ background: "var(--bg-surface)", border: "1px solid var(--border)", borderRadius: "8px", overflow: "hidden", borderLeft: "3px solid var(--primary)" }} className="animate-in">

        {/* Question header */}
        <div style={{ padding: "12px 16px 12px 18px", borderBottom: "1px solid var(--border)", display: "flex", alignItems: "flex-start", justifyContent: "space-between", gap: 12 }}>
          <div style={{ flex: 1, minWidth: 0 }}>
            <p style={{ fontSize: "11px", fontWeight: 600, color: "var(--text-accent)", textTransform: "uppercase", letterSpacing: "0.06em", margin: "0 0 5px" }}>
              Your question
            </p>
            <p style={{ fontSize: "13px", fontWeight: 500, color: "var(--text-primary)", margin: 0 }}>{result.question}</p>
          </div>

          {/* Save / bookmark button */}
          {onSave && (
            <button
              onClick={handleSave}
              disabled={saveState !== "idle"}
              title={saveState === "saved" ? "Saved to history" : "Save to history"}
              style={{
                flexShrink: 0,
                display: "flex", alignItems: "center", gap: 5,
                padding: "5px 10px", borderRadius: 5,
                border: `1px solid ${saveState === "saved" ? "rgba(124,58,237,0.4)" : "var(--border)"}`,
                background: saveState === "saved" ? "rgba(124,58,237,0.1)" : "none",
                color: saveState === "saved" ? "var(--text-accent)" : "var(--text-muted)",
                fontSize: 12, cursor: saveState === "idle" ? "pointer" : "default",
                transition: "all 0.15s",
              }}
              onMouseEnter={(e) => { if (saveState === "idle") { (e.currentTarget as HTMLElement).style.borderColor = "var(--primary)"; (e.currentTarget as HTMLElement).style.color = "var(--text-primary)"; } }}
              onMouseLeave={(e) => { if (saveState === "idle") { (e.currentTarget as HTMLElement).style.borderColor = "var(--border)"; (e.currentTarget as HTMLElement).style.color = "var(--text-muted)"; } }}
            >
              {saveState === "saving" ? (
                <>
                  <div className="animate-spin" style={{ width: 12, height: 12, border: "2px solid var(--border)", borderTopColor: "var(--primary)", borderRadius: "50%" }} />
                  Saving…
                </>
              ) : (
                <>
                  <BookmarkIcon filled={saveState === "saved"} />
                  {saveState === "saved" ? "Saved" : "Save"}
                </>
              )}
            </button>
          )}
        </div>

        {/* Answer body */}
        <div style={{ padding: "16px 18px", display: "flex", flexDirection: "column", gap: "16px" }}>
          <p style={{ fontSize: "13px", color: "var(--text-secondary)", margin: 0, lineHeight: 1.7, whiteSpace: "pre-wrap" }}>
            {result.answer}
          </p>

          {domains.length > 0 && (
            <div>
              <p style={{ fontSize: "11px", color: "var(--text-muted)", margin: "0 0 7px" }}>Domains checked</p>
              <div style={{ display: "flex", flexWrap: "wrap", gap: "5px" }}>
                {domains.map((d) => (
                  <span key={d} style={{ fontSize: "11px", padding: "2px 8px", borderRadius: "4px", background: "rgba(124,58,237,0.1)", border: "1px solid rgba(124,58,237,0.25)", color: "var(--text-accent)" }}>
                    {DOMAIN_LABELS[d] ?? d}
                  </span>
                ))}
              </div>
            </div>
          )}

          {commands.length > 0 && (
            <div>
              <p style={{ fontSize: "11px", color: "var(--text-muted)", margin: "0 0 7px" }}>Fix commands</p>
              <div style={{ display: "flex", flexDirection: "column", gap: "6px" }}>
                {commands.map((cmd, i) => (
                  <div key={i} style={{ display: "flex", alignItems: "flex-start", gap: "8px", background: "#010409", border: "1px solid #1c2028", borderRadius: "5px", padding: "8px 12px" }}>
                    <span style={{ fontSize: "12px", fontFamily: "var(--font-mono, monospace)", color: "#52525b", flexShrink: 0, marginTop: "1px", userSelect: "none" }}>$</span>
                    <code style={{ flex: 1, fontSize: "13px", color: "#7ee787", fontFamily: "var(--font-mono, monospace)", wordBreak: "break-all", lineHeight: 1.5 }}>{cmd}</code>
                    <CopyButton text={cmd} />
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>

        {/* Footer */}
        <div style={{ padding: "10px 18px", borderTop: "1px solid var(--border)", display: "flex", alignItems: "center", gap: "10px", flexWrap: "wrap" }}>
          <span style={{ fontSize: "12px", color: "var(--text-muted)" }}>Confidence</span>
          <div style={{ display: "flex", alignItems: "center", gap: "6px" }}>
            <div style={{ height: "4px", width: "72px", background: "var(--bg-surface-2)", borderRadius: "2px", overflow: "hidden" }}>
              <div style={{ height: "100%", background: "var(--primary)", borderRadius: "2px", width: `${result.confidence}%`, transition: "width 0.5s ease" }} />
            </div>
            <span style={{ fontSize: "12px", color: "var(--text-secondary)", fontWeight: 500 }}>{result.confidence}%</span>
          </div>
          <span style={{ color: "var(--border)", fontSize: "14px" }}>·</span>
          <span style={{ ...(severityStyle), display: "inline-flex", alignItems: "center", padding: "2px 8px", borderRadius: "4px", fontSize: "11px", fontWeight: 700, textTransform: "uppercase", letterSpacing: "0.05em" }}>
            {severityKey}
          </span>
          {result.investigation?.target_resource && (
            <>
              <span style={{ color: "var(--border)", fontSize: "14px" }}>·</span>
              <span style={{ fontSize: "12px", color: "var(--text-muted)" }}>
                Target: <code style={{ color: "var(--text-secondary)", fontFamily: "var(--font-mono, monospace)" }}>{result.investigation.target_resource}</code>
              </span>
            </>
          )}
        </div>
      </div>
    </>
  );
}
