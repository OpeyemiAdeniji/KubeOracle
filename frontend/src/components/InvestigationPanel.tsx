"use client";

import { Step, AppStatus, INVESTIGATION_STEPS } from "@/types";

interface InvestigationPanelProps {
  steps: Step[];
  status: AppStatus;
}

function StepIcon({ status }: { status: Step["status"] }) {
  if (status === "complete") {
    return (
      <div style={{ width: "22px", height: "22px", borderRadius: "50%", background: "rgba(74,222,128,0.15)", border: "1px solid rgba(74,222,128,0.4)", display: "flex", alignItems: "center", justifyContent: "center", flexShrink: 0 }}>
        <svg width="12" height="12" fill="none" viewBox="0 0 24 24" stroke="#4ade80">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2.5} d="M5 13l4 4L19 7" />
        </svg>
      </div>
    );
  }
  if (status === "failed") {
    return (
      <div style={{ width: "22px", height: "22px", borderRadius: "50%", background: "rgba(239,68,68,0.15)", border: "1px solid rgba(239,68,68,0.4)", display: "flex", alignItems: "center", justifyContent: "center", flexShrink: 0 }}>
        <svg width="12" height="12" fill="none" viewBox="0 0 24 24" stroke="#f87171">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2.5} d="M6 18L18 6M6 6l12 12" />
        </svg>
      </div>
    );
  }
  if (status === "active") {
    return (
      <div style={{ width: "22px", height: "22px", borderRadius: "50%", background: "rgba(124,58,237,0.15)", border: "1px solid rgba(124,58,237,0.5)", display: "flex", alignItems: "center", justifyContent: "center", flexShrink: 0 }} className="animate-step-pulse">
        <div style={{ width: "7px", height: "7px", borderRadius: "50%", background: "var(--primary)" }} />
      </div>
    );
  }
  return (
    <div style={{ width: "22px", height: "22px", borderRadius: "50%", border: "1px solid var(--border)", display: "flex", alignItems: "center", justifyContent: "center", flexShrink: 0 }}>
      <div style={{ width: "5px", height: "5px", borderRadius: "50%", background: "var(--text-muted)" }} />
    </div>
  );
}

export default function InvestigationPanel({ steps, status }: InvestigationPanelProps) {
  const completed = steps.filter((s) => s.status === "complete").length;
  const total = INVESTIGATION_STEPS.length;
  const progressPct = Math.round((completed / total) * 100);

  const statusLabel =
    status === "investigating" ? "Investigating…"
    : status === "success"    ? "Complete"
    : status === "error"      ? "Failed"
    : "Ready";

  const statusColor =
    status === "investigating" ? "var(--text-accent)"
    : status === "success"    ? "#4ade80"
    : status === "error"      ? "#f87171"
    : "var(--text-muted)";

  return (
    <div style={{ background: "var(--bg-surface)", border: "1px solid var(--border)", borderRadius: "8px", overflow: "hidden" }}>
      {/* Panel header */}
      <div style={{ padding: "14px 18px", borderBottom: "1px solid var(--border)", display: "flex", alignItems: "center", justifyContent: "space-between" }}>
        <div>
          <h2 style={{ fontSize: "13px", fontWeight: 600, color: "var(--text-primary)", margin: "0 0 2px" }}>Investigation Progress</h2>
          <p style={{ fontSize: "12px", color: statusColor, margin: 0 }}>{statusLabel}</p>
        </div>
        <div style={{ textAlign: "right" }}>
          <span style={{ fontSize: "14px", fontWeight: 700, color: "var(--text-primary)" }}>{completed}</span>
          <span style={{ fontSize: "14px", color: "var(--text-muted)" }}>/{total}</span>
        </div>
      </div>

      {/* Progress bar */}
      <div style={{ height: "3px", background: "var(--bg-surface-2)" }}>
        <div style={{ height: "100%", background: "var(--primary)", transition: "width 0.7s ease-out", width: `${progressPct}%` }} />
      </div>

      {/* Steps */}
      <div>
        {steps.map((step) => (
          <div key={step.id}
            style={{
              display: "flex",
              alignItems: "center",
              gap: "10px",
              padding: "9px 18px",
              borderBottom: "1px solid var(--border-subtle)",
              background: step.status === "active" ? "rgba(124,58,237,0.08)" : "transparent",
              transition: "background 0.15s",
            }}>
            <StepIcon status={step.status} />
            <span style={{
              fontSize: "12px",
              flex: 1,
              color: step.status === "active"   ? "var(--text-primary)"
                   : step.status === "complete" ? "var(--text-secondary)"
                   : step.status === "failed"   ? "#f87171"
                   : "var(--text-muted)",
              fontWeight: step.status === "active" ? 500 : 400,
            }}>
              {step.label}
            </span>
            {step.status === "active" && (
              <div style={{ display: "flex", gap: "3px" }}>
                {[0, 1, 2].map((i) => (
                  <div key={i} className="animate-bounce"
                    style={{ width: "4px", height: "4px", borderRadius: "50%", background: "var(--primary)", animationDelay: `${i * 150}ms` }} />
                ))}
              </div>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}
