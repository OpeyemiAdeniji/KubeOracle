"use client";

import { useState, useMemo } from "react";
import { Investigation, Diagnosis } from "@/types";

interface HistoryTableProps {
  history: Investigation[];
  onDeleteItem: (id: number) => Promise<void>;
  loading?: boolean;
}

const PAGE_SIZE = 5;

const SEVERITY_INLINE: Record<string, React.CSSProperties> = {
  critical: { background: "#450a0a", color: "#f87171", border: "1px solid #7f1d1d" },
  high:     { background: "#431407", color: "#fb923c", border: "1px solid #7c2d12" },
  medium:   { background: "#422006", color: "#facc15", border: "1px solid #713f12" },
  low:      { background: "#052e16", color: "#4ade80", border: "1px solid #14532d" },
};

const SEVERITY_FILTERS = ["all", "critical", "high", "medium", "low"];

function formatDate(iso: string): string {
  try {
    return new Date(iso).toLocaleDateString("en-US", { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
  } catch { return iso; }
}

// ── Skeleton ──────────────────────────────────────────────────────────────────

function SkeletonTable() {
  return (
    <div style={{ background: "var(--bg-surface)", border: "1px solid var(--border)", borderRadius: "8px", overflow: "hidden" }}>
      <div style={{ padding: "14px 18px", borderBottom: "1px solid var(--border)" }}>
        <div className="skeleton-shimmer" style={{ height: "14px", width: "140px", borderRadius: "4px" }} />
      </div>
      <div>
        {Array.from({ length: 3 }).map((_, i) => (
          <div key={i} style={{ padding: "12px 18px", display: "flex", alignItems: "center", gap: "16px", borderBottom: "1px solid var(--border-subtle)" }}>
            <div className="skeleton-shimmer" style={{ height: "12px", width: "80px", borderRadius: "4px" }} />
            <div className="skeleton-shimmer" style={{ height: "20px", width: "60px", borderRadius: "4px" }} />
            <div className="skeleton-shimmer" style={{ height: "12px", flex: 1, borderRadius: "4px" }} />
          </div>
        ))}
      </div>
    </div>
  );
}

// ── Empty state ────────────────────────────────────────────────────────────────

function EmptyState() {
  return (
    <div style={{ background: "var(--bg-surface)", border: "1px solid var(--border)", borderRadius: "8px", padding: "48px 24px", display: "flex", flexDirection: "column", alignItems: "center", gap: "16px", textAlign: "center" }}>
      <div style={{ position: "relative" }}>
        <svg viewBox="0 0 120 100" style={{ width: "120px", height: "auto", opacity: 0.35, animation: "floatUp 4s ease-in-out infinite alternate" }}>
          <ellipse cx="60" cy="75" rx="28" ry="6" fill="#374151" />
          <rect x="46" y="40" width="28" height="36" rx="5" fill="#4b5563" />
          <circle cx="60" cy="32" r="16" fill="none" stroke="#7c3aed" strokeWidth="3" />
          <circle cx="60" cy="32" r="10" fill="#1e1b4b" opacity="0.7" />
          <circle cx="56" cy="28" r="2.5" fill="#a78bfa" opacity="0.6" />
          {[[20,15],[85,10],[95,45],[15,55],[75,20]].map(([x, y], i) => (
            <circle key={i} cx={x} cy={y} r="2" fill="#a78bfa" opacity={0.3 + i * 0.1}
              style={{ animation: `stepPulse ${1.5 + i * 0.3}s ease-in-out infinite`, animationDelay: `${i * 0.4}s` }} />
          ))}
        </svg>
      </div>
      <div>
        <p style={{ fontSize: "14px", fontWeight: 500, color: "var(--text-secondary)", margin: "0 0 4px" }}>No investigations yet</p>
        <p style={{ fontSize: "13px", color: "var(--text-muted)", margin: 0 }}>Run your first investigation to see history here</p>
      </div>
    </div>
  );
}

// ── Severity trend mini chart ─────────────────────────────────────────────────

function SeverityTrendChart({ history }: { history: Investigation[] }) {
  const last10 = [...history].slice(0, 10).reverse();
  if (last10.length < 2) return null;

  const W = 300; const H = 44; const PAD = 8; const DOT = 4;
  const usableW = W - PAD * 2;
  const yMap: Record<string, number> = { critical: H - 6, high: H - 18, medium: H - 30, low: H - 42 };
  const colorMap: Record<string, string> = { critical: "#ef4444", high: "#f97316", medium: "#eab308", low: "#22c55e" };
  const pts = last10.map((inv, i) => ({
    x: PAD + (last10.length === 1 ? usableW / 2 : (i / (last10.length - 1)) * usableW),
    y: yMap[inv.severity] ?? H / 2,
    color: colorMap[inv.severity] ?? "#6b7280",
    label: `${inv.severity} · ${formatDate(inv.created_at)}`,
  }));
  const polyline = pts.map((p) => `${p.x},${p.y}`).join(" ");

  return (
    <div style={{ padding: "10px 18px", borderBottom: "1px solid var(--border)" }}>
      <p style={{ fontSize: "10px", color: "var(--text-muted)", textTransform: "uppercase", letterSpacing: "0.06em", margin: "0 0 8px" }}>
        Severity Trend (last {last10.length})
      </p>
      <svg viewBox={`0 0 ${W} ${H}`} style={{ width: "100%", height: "44px" }}>
        <polyline points={polyline} fill="none" stroke="var(--border)" strokeWidth="1.5" strokeDasharray="4 3" />
        {pts.map((p, i) => (
          <g key={i}>
            <circle cx={p.x} cy={p.y} r={DOT} fill={p.color} />
            <title>{p.label}</title>
          </g>
        ))}
      </svg>
    </div>
  );
}

// ── Modal export button ───────────────────────────────────────────────────────

function ExportButton({ diagnosis }: { diagnosis: Diagnosis | null }) {
  if (!diagnosis) return null;
  const md = `# KubeOracle Diagnosis\n\n**Severity:** ${diagnosis.severity.toUpperCase()}\n**Confidence:** ${diagnosis.confidence}%\n\n## Root Cause\n${diagnosis.root_cause}\n\n## Explanation\n${diagnosis.explanation}\n\n## Fix\n${diagnosis.fix}\n\n## kubectl Commands\n${diagnosis.kubectl_commands.map((c) => `\`\`\`bash\n${c}\n\`\`\``).join("\n")}\n\n## Contributing Factors\n${diagnosis.contributing_factors.map((f) => `- ${f}`).join("\n")}\n\n## Prevention\n${diagnosis.prevention}\n`;
  const download = () => {
    const blob = new Blob([md], { type: "text/markdown" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url; a.download = `kubeoracle-diagnosis-${Date.now()}.md`; a.click();
    URL.revokeObjectURL(url);
  };
  return (
    <button onClick={download}
      style={{ display: "flex", alignItems: "center", gap: "5px", padding: "5px 10px", borderRadius: "5px", fontSize: "12px", color: "var(--text-secondary)", background: "none", border: "1px solid var(--border)", cursor: "pointer" }}>
      <svg width="12" height="12" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4" /></svg>
      Export .md
    </button>
  );
}

// ── Investigation modal ───────────────────────────────────────────────────────

function InvestigationModal({ inv, onClose, onDelete }: { inv: Investigation; onClose: () => void; onDelete: () => void }) {
  const d = inv.full_diagnosis;
  const sev = SEVERITY_INLINE[inv.severity] ?? SEVERITY_INLINE.low;

  return (
    <div style={{ position: "fixed", inset: 0, zIndex: 50, display: "flex", alignItems: "center", justifyContent: "center", padding: "16px" }} onClick={onClose}>
      <div style={{ position: "absolute", inset: 0, background: "rgba(0,0,0,0.7)", backdropFilter: "blur(4px)" }} />
      <div style={{ position: "relative", background: "var(--bg-surface)", border: "1px solid var(--border)", borderRadius: "10px", width: "100%", maxWidth: "640px", maxHeight: "90vh", display: "flex", flexDirection: "column", zIndex: 10 }} onClick={(e) => e.stopPropagation()}>
        {/* Modal header */}
        <div style={{ padding: "14px 20px", borderBottom: "1px solid var(--border)", display: "flex", alignItems: "center", justifyContent: "space-between", gap: "12px", flexWrap: "wrap", flexShrink: 0 }}>
          <div style={{ display: "flex", alignItems: "center", gap: "10px", flexWrap: "wrap" }}>
            <span style={{ ...sev, display: "inline-flex", alignItems: "center", padding: "3px 10px", borderRadius: "4px", fontSize: "11px", fontWeight: 700, textTransform: "uppercase", letterSpacing: "0.06em" }}>
              {inv.severity}
            </span>
            <span style={{ fontSize: "12px", color: "var(--text-muted)" }}>{formatDate(inv.created_at)}</span>
            {inv.context && (
              <span style={{ fontSize: "11px", color: "var(--text-accent)", background: "rgba(124,58,237,0.12)", border: "1px solid rgba(124,58,237,0.3)", borderRadius: "4px", padding: "2px 8px" }}>
                {inv.context}
              </span>
            )}
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
            <ExportButton diagnosis={d} />
            <button onClick={onDelete}
              style={{ fontSize: "12px", color: "#f87171", border: "1px solid #7f1d1d", background: "none", borderRadius: "5px", padding: "5px 10px", cursor: "pointer" }}>
              Delete
            </button>
            <button onClick={onClose}
              style={{ color: "var(--text-muted)", background: "none", border: "none", cursor: "pointer", display: "flex", alignItems: "center" }}>
              <svg width="18" height="18" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" /></svg>
            </button>
          </div>
        </div>

        {/* Modal body */}
        <div style={{ overflowY: "auto", padding: "20px", display: "flex", flexDirection: "column", gap: "18px", flex: 1 }}>
          <div style={{ display: "flex", alignItems: "flex-start", gap: "16px" }}>
            <div style={{ flexShrink: 0, textAlign: "center" }}>
              <div style={{ fontSize: "28px", fontWeight: 700, color: d?.confidence >= 70 ? "#4ade80" : d?.confidence >= 40 ? "#facc15" : "#f87171" }}>
                {d?.confidence ?? 0}
              </div>
              <div style={{ fontSize: "10px", color: "var(--text-muted)" }}>conf.</div>
            </div>
            <div>
              <p style={{ fontSize: "11px", color: "var(--text-muted)", textTransform: "uppercase", letterSpacing: "0.06em", margin: "0 0 5px" }}>Root Cause</p>
              <p style={{ fontSize: "14px", fontWeight: 500, color: "var(--text-primary)", margin: 0, lineHeight: 1.6 }}>{d?.root_cause ?? inv.root_cause}</p>
            </div>
          </div>

          {d?.explanation && (
            <div>
              <p style={{ fontSize: "11px", color: "var(--text-muted)", textTransform: "uppercase", letterSpacing: "0.06em", margin: "0 0 5px" }}>Explanation</p>
              <p style={{ fontSize: "13px", color: "var(--text-secondary)", margin: 0, lineHeight: 1.6 }}>{d.explanation}</p>
            </div>
          )}

          {d?.fix && (
            <div>
              <p style={{ fontSize: "11px", color: "var(--text-muted)", textTransform: "uppercase", letterSpacing: "0.06em", margin: "0 0 5px" }}>Fix</p>
              <p style={{ fontSize: "13px", color: "var(--text-secondary)", margin: 0, lineHeight: 1.6 }}>{d.fix}</p>
            </div>
          )}

          {d?.kubectl_commands?.length > 0 && (
            <div>
              <p style={{ fontSize: "11px", color: "var(--text-muted)", textTransform: "uppercase", letterSpacing: "0.06em", margin: "0 0 8px" }}>kubectl Commands</p>
              <div style={{ display: "flex", flexDirection: "column", gap: "6px" }}>
                {d.kubectl_commands.map((cmd, i) => (
                  <div key={i} style={{ display: "flex", alignItems: "flex-start", gap: "8px", background: "#010409", border: "1px solid #1c2028", borderRadius: "5px", padding: "8px 12px" }}>
                    <span style={{ fontSize: "12px", fontFamily: "var(--font-mono, monospace)", color: "#52525b", flexShrink: 0, userSelect: "none" }}>$</span>
                    <code style={{ flex: 1, fontSize: "13px", color: "#7ee787", fontFamily: "var(--font-mono, monospace)", wordBreak: "break-all", lineHeight: 1.5 }}>{cmd}</code>
                  </div>
                ))}
              </div>
            </div>
          )}

          {d?.contributing_factors?.length > 0 && (
            <div>
              <p style={{ fontSize: "11px", color: "var(--text-muted)", textTransform: "uppercase", letterSpacing: "0.06em", margin: "0 0 5px" }}>Contributing Factors</p>
              <ul style={{ margin: 0, padding: 0, listStyle: "none", display: "flex", flexDirection: "column", gap: "4px" }}>
                {d.contributing_factors.map((f, i) => (
                  <li key={i} style={{ display: "flex", alignItems: "flex-start", gap: "8px", fontSize: "13px", color: "var(--text-secondary)" }}>
                    <span style={{ color: "var(--primary)", marginTop: "2px" }}>•</span>{f}
                  </li>
                ))}
              </ul>
            </div>
          )}

          {d?.prevention && (
            <div>
              <p style={{ fontSize: "11px", color: "var(--text-muted)", textTransform: "uppercase", letterSpacing: "0.06em", margin: "0 0 5px" }}>Prevention</p>
              <p style={{ fontSize: "13px", color: "var(--text-secondary)", margin: 0, lineHeight: 1.6, fontStyle: "italic", borderLeft: "2px solid var(--border)", paddingLeft: "12px" }}>{d.prevention}</p>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

// ── Main component ────────────────────────────────────────────────────────────

export default function HistoryTable({ history, onDeleteItem, loading }: HistoryTableProps) {
  const [severityFilter, setSeverityFilter] = useState("all");
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(1);
  const [selectedInv, setSelectedInv] = useState<Investigation | null>(null);
  const [deleting, setDeleting] = useState<number | null>(null);

  const filtered = useMemo(() => {
    let result = history;
    if (severityFilter !== "all") result = result.filter((h) => h.severity === severityFilter);
    if (search.trim()) {
      const q = search.toLowerCase();
      result = result.filter((h) =>
        h.root_cause.toLowerCase().includes(q) ||
        (h.context || "").toLowerCase().includes(q)
      );
    }
    return result;
  }, [history, severityFilter, search]);

  const totalPages = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE));
  const currentPage = Math.min(page, totalPages);
  const paginated = filtered.slice((currentPage - 1) * PAGE_SIZE, currentPage * PAGE_SIZE);

  const resetPage = () => setPage(1);

  const handleDelete = async (id: number) => {
    setDeleting(id);
    await onDeleteItem(id);
    setDeleting(null);
    setSelectedInv(null);
  };

  if (loading) return <SkeletonTable />;
  if (history.length === 0) return <EmptyState />;

  return (
    <>
      {selectedInv && (
        <InvestigationModal
          inv={selectedInv}
          onClose={() => setSelectedInv(null)}
          onDelete={() => handleDelete(selectedInv.id)}
        />
      )}

      <div style={{ background: "var(--bg-surface)", border: "1px solid var(--border)", borderRadius: "8px", overflow: "hidden" }}>
        {/* Header */}
        <div style={{ padding: "14px 18px", borderBottom: "1px solid var(--border)", display: "flex", alignItems: "center", justifyContent: "space-between", gap: "12px", flexWrap: "wrap" }}>
          <h2 style={{ fontSize: "13px", fontWeight: 600, color: "var(--text-primary)", margin: 0 }}>Investigation History</h2>
          <span style={{ fontSize: "12px", color: "var(--text-muted)" }}>
            {filtered.length !== history.length
              ? `${filtered.length} of ${history.length} records`
              : `${history.length} record${history.length !== 1 ? "s" : ""}`}
          </span>
        </div>

        {/* Trend chart */}
        <SeverityTrendChart history={history} />

        {/* Filters */}
        <div style={{ padding: "10px 18px", borderBottom: "1px solid var(--border)", display: "flex", alignItems: "center", gap: "10px", flexWrap: "wrap" }}>
          <div style={{ display: "flex", gap: "5px", flexWrap: "wrap" }}>
            {SEVERITY_FILTERS.map((f) => (
              <button key={f} onClick={() => { setSeverityFilter(f); resetPage(); }}
                style={{
                  padding: "4px 10px",
                  borderRadius: "4px",
                  fontSize: "12px",
                  fontWeight: severityFilter === f ? 600 : 400,
                  border: `1px solid ${severityFilter === f && f !== "all" ? (SEVERITY_INLINE[f]?.border as string || "var(--border)") : "var(--border)"}`,
                  background: severityFilter === f
                    ? f === "all" ? "var(--primary)" : (SEVERITY_INLINE[f]?.background as string || "var(--bg-surface-2)")
                    : "var(--bg-surface-2)",
                  color: severityFilter === f
                    ? f === "all" ? "#fff" : (SEVERITY_INLINE[f]?.color as string || "var(--text-primary)")
                    : "var(--text-muted)",
                  cursor: "pointer",
                  textTransform: "capitalize",
                }}>
                {f}
              </button>
            ))}
          </div>
          <div style={{ position: "relative", flex: 1, minWidth: "140px" }}>
            <svg style={{ position: "absolute", left: "9px", top: "50%", transform: "translateY(-50%)", color: "var(--text-muted)", pointerEvents: "none" }} width="12" height="12" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
            </svg>
            <input type="text" value={search} onChange={(e) => { setSearch(e.target.value); resetPage(); }}
              placeholder="Search root cause…"
              style={{ width: "100%", background: "var(--bg-surface-2)", border: "1px solid var(--border)", borderRadius: "5px", padding: "5px 10px 5px 26px", fontSize: "12px", color: "var(--text-primary)", outline: "none" }}
              onFocus={(e) => (e.target.style.borderColor = "var(--primary)")}
              onBlur={(e) => (e.target.style.borderColor = "var(--border)")} />
          </div>
        </div>

        {/* Table or empty filter */}
        {filtered.length === 0 ? (
          <div style={{ padding: "28px", textAlign: "center", color: "var(--text-muted)", fontSize: "13px" }}>No records match your filters</div>
        ) : (
          <>
            <div style={{ overflowX: "auto" }}>
              <table style={{ width: "100%", borderCollapse: "collapse", fontSize: "13px" }}>
                <thead>
                  <tr style={{ borderBottom: "1px solid var(--border)" }}>
                    {["Date", "Cluster", "Severity", "Confidence", "Root Cause", ""].map((h, i) => (
                      <th key={i} style={{ padding: "10px 18px", textAlign: "left", fontSize: "11px", fontWeight: 600, color: "var(--text-muted)", textTransform: "uppercase", letterSpacing: "0.05em", whiteSpace: "nowrap" }}>{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {paginated.map((inv) => (
                    <tr key={inv.id}
                      style={{ borderBottom: "1px solid var(--border-subtle)", cursor: "pointer" }}
                      onClick={() => setSelectedInv(inv)}
                      onMouseEnter={(e) => ((e.currentTarget as HTMLElement).style.background = "var(--bg-hover)")}
                      onMouseLeave={(e) => ((e.currentTarget as HTMLElement).style.background = "transparent")}
                    >
                      <td style={{ padding: "10px 18px", color: "var(--text-muted)", whiteSpace: "nowrap" }}>{formatDate(inv.created_at)}</td>
                      <td style={{ padding: "10px 18px", color: "var(--text-secondary)", maxWidth: "120px", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                        {inv.context || <span style={{ color: "var(--text-muted)" }}>—</span>}
                      </td>
                      <td style={{ padding: "10px 18px" }}>
                        <span style={{ ...(SEVERITY_INLINE[inv.severity] ?? SEVERITY_INLINE.low), display: "inline-flex", alignItems: "center", padding: "2px 8px", borderRadius: "4px", fontSize: "11px", fontWeight: 700, textTransform: "uppercase", letterSpacing: "0.05em" }}>
                          {inv.severity}
                        </span>
                      </td>
                      <td style={{ padding: "10px 18px" }}>
                        <div style={{ display: "flex", alignItems: "center", gap: "6px" }}>
                          <div style={{ width: "56px", height: "5px", background: "var(--bg-surface-2)", borderRadius: "3px", overflow: "hidden" }}>
                            <div style={{ height: "100%", borderRadius: "3px", background: inv.confidence >= 70 ? "#4ade80" : inv.confidence >= 40 ? "#facc15" : "#f87171", width: `${inv.confidence}%` }} />
                          </div>
                          <span style={{ fontSize: "12px", color: "var(--text-secondary)" }}>{inv.confidence}%</span>
                        </div>
                      </td>
                      <td style={{ padding: "10px 18px", color: "var(--text-secondary)", maxWidth: "260px", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                        {inv.root_cause}
                      </td>
                      <td style={{ padding: "10px 18px" }} onClick={(e) => e.stopPropagation()}>
                        <button onClick={() => handleDelete(inv.id)} disabled={deleting === inv.id}
                          style={{ color: "var(--text-muted)", background: "none", border: "none", cursor: deleting === inv.id ? "not-allowed" : "pointer", opacity: deleting === inv.id ? 0.4 : 1, display: "flex", alignItems: "center" }}
                          title="Delete"
                          onMouseEnter={(e) => ((e.currentTarget as HTMLElement).style.color = "#f87171")}
                          onMouseLeave={(e) => ((e.currentTarget as HTMLElement).style.color = "var(--text-muted)")}
                        >
                          {deleting === inv.id ? (
                            <div className="animate-spin" style={{ width: "14px", height: "14px", border: "1.5px solid var(--border)", borderTopColor: "var(--text-muted)", borderRadius: "50%" }} />
                          ) : (
                            <svg width="14" height="14" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" /></svg>
                          )}
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            {/* Pagination */}
            {totalPages > 1 && (
              <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", padding: "10px 18px", borderTop: "1px solid var(--border)" }}>
                <button onClick={() => setPage((p) => Math.max(1, p - 1))} disabled={currentPage === 1}
                  style={{ fontSize: "12px", color: "var(--text-secondary)", background: "none", border: "none", cursor: currentPage === 1 ? "not-allowed" : "pointer", opacity: currentPage === 1 ? 0.3 : 1, padding: "5px 10px", borderRadius: "4px" }}>
                  ← Previous
                </button>
                <span style={{ fontSize: "12px", color: "var(--text-muted)" }}>Page {currentPage} of {totalPages}</span>
                <button onClick={() => setPage((p) => Math.min(totalPages, p + 1))} disabled={currentPage === totalPages}
                  style={{ fontSize: "12px", color: "var(--text-secondary)", background: "none", border: "none", cursor: currentPage === totalPages ? "not-allowed" : "pointer", opacity: currentPage === totalPages ? 0.3 : 1, padding: "5px 10px", borderRadius: "4px" }}>
                  Next →
                </button>
              </div>
            )}
          </>
        )}
      </div>
    </>
  );
}
