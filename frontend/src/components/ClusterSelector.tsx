"use client";

import { useState, useMemo, useEffect } from "react";
import { Cluster, Investigation } from "@/types";
import { parseClusterContext, PROVIDER_BADGE } from "@/lib/clusterUtils";

interface ClusterSelectorProps {
  clusters: Cluster[];
  selected: string;
  onSelect: (context: string) => void;
  loading: boolean;
  error: string | null;
  investigations: Investigation[];
}

const PAGE_SIZE = 5;

function SkeletonRow() {
  return (
    <div style={{ display: "flex", alignItems: "center", gap: "10px", padding: "10px 12px", borderRadius: "6px", border: "1px solid var(--border)" }}>
      <div className="skeleton-shimmer" style={{ width: "14px", height: "14px", borderRadius: "50%" }} />
      <div style={{ flex: 1, display: "flex", flexDirection: "column", gap: "5px" }}>
        <div className="skeleton-shimmer" style={{ height: "11px", width: "70%", borderRadius: "3px" }} />
        <div className="skeleton-shimmer" style={{ height: "9px", width: "50%", borderRadius: "3px" }} />
      </div>
    </div>
  );
}

function HealthDot({ severity, tooltip }: { severity: string | null; tooltip: string }) {
  const colors: Record<string, string> = {
    critical: "#ef4444", high: "#f97316", medium: "#facc15", low: "#4ade80",
  };
  const color = severity ? (colors[severity] ?? "#52525b") : "#3a3a45";

  return (
    <div style={{ position: "relative" }} className="group">
      <div style={{ width: "8px", height: "8px", borderRadius: "50%", background: color, flexShrink: 0 }} title={tooltip} />
    </div>
  );
}

export default function ClusterSelector({
  clusters, selected, onSelect, loading, error, investigations,
}: ClusterSelectorProps) {
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(1);

  // When clusters load or selection changes (e.g. from ?context= URL param),
  // jump to the page that contains the selected cluster.
  // Skip when a search query is active — filtered results use their own pagination.
  useEffect(() => {
    if (!selected || !clusters.length || search.trim()) return;
    const idx = clusters.findIndex((c) => c.context === selected);
    if (idx < 0) return;
    const targetPage = Math.ceil((idx + 1) / PAGE_SIZE);
    setPage(targetPage);
  }, [clusters, selected, search]);

  const healthMap = useMemo(() => {
    const map: Record<string, { severity: string; lastSeen: string }> = {};
    investigations.forEach((inv) => {
      const ctx = inv.context || "";
      if (ctx && !map[ctx]) map[ctx] = { severity: inv.severity, lastSeen: inv.created_at };
    });
    return map;
  }, [investigations]);

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    if (!q) return clusters;
    return clusters.filter((c) => {
      const { displayName, region } = parseClusterContext(c.context);
      return (
        c.name.toLowerCase().includes(q) ||
        c.server.toLowerCase().includes(q) ||
        displayName.toLowerCase().includes(q) ||
        (region && region.includes(q))
      );
    });
  }, [clusters, search]);

  const totalPages = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE));
  const currentPage = Math.min(page, totalPages);
  const paginated = filtered.slice((currentPage - 1) * PAGE_SIZE, currentPage * PAGE_SIZE);
  const setSearchAndReset = (v: string) => { setSearch(v); setPage(1); };

  if (loading) {
    return (
      <div style={{ background: "var(--bg-surface)", border: "1px solid var(--border)", borderRadius: "8px", padding: "16px" }}>
        <div className="skeleton-shimmer" style={{ height: "12px", width: "100px", borderRadius: "3px", marginBottom: "12px" }} />
        <div className="skeleton-shimmer" style={{ height: "34px", borderRadius: "6px", marginBottom: "10px" }} />
        <div style={{ display: "flex", flexDirection: "column", gap: "6px" }}>
          {Array.from({ length: 3 }).map((_, i) => <SkeletonRow key={i} />)}
        </div>
      </div>
    );
  }

  return (
    <div style={{ background: "var(--bg-surface)", border: "1px solid var(--border)", borderRadius: "8px", padding: "16px" }}>
      <h2 style={{ fontSize: "13px", fontWeight: 600, color: "var(--text-secondary)", margin: "0 0 12px" }}>Select Cluster</h2>

      {/* Search */}
      <div style={{ position: "relative", marginBottom: "10px" }}>
        <svg style={{ position: "absolute", left: "10px", top: "50%", transform: "translateY(-50%)", color: "var(--text-muted)", pointerEvents: "none" }} width="13" height="13" fill="none" viewBox="0 0 24 24" stroke="currentColor">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
        </svg>
        <input type="text" value={search} onChange={(e) => setSearchAndReset(e.target.value)}
          placeholder="Search clusters…"
          style={{ width: "100%", background: "var(--bg-surface-2)", border: "1px solid var(--border)", borderRadius: "6px", padding: "7px 32px 7px 32px", fontSize: "13px", color: "var(--text-primary)", outline: "none" }}
          onFocus={(e) => (e.target.style.borderColor = "var(--primary)")}
          onBlur={(e) => (e.target.style.borderColor = "var(--border)")} />
        {search && (
          <button onClick={() => setSearchAndReset("")}
            style={{ position: "absolute", right: "8px", top: "50%", transform: "translateY(-50%)", background: "none", border: "none", color: "var(--text-muted)", cursor: "pointer", display: "flex", alignItems: "center" }}>
            <svg width="13" height="13" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" /></svg>
          </button>
        )}
      </div>

      {/* Empty states */}
      {!filtered.length ? (
        <div style={{ padding: "12px 0" }}>
          {!clusters.length ? (
            <div style={{ display: "flex", alignItems: "flex-start", gap: "10px" }}>
              <div style={{ width: "32px", height: "32px", borderRadius: "6px", background: "#422006", border: "1px solid #713f12", display: "flex", alignItems: "center", justifyContent: "center", flexShrink: 0, marginTop: "2px" }}>
                <svg width="16" height="16" fill="none" viewBox="0 0 24 24" stroke="#facc15"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" /></svg>
              </div>
              <div>
                <p style={{ fontSize: "13px", fontWeight: 500, color: "#facc15", margin: "0 0 3px" }}>No clusters found</p>
                <p style={{ fontSize: "13px", color: "var(--text-secondary)", margin: "0 0 8px" }}>{error || "No Kubernetes contexts in kubeconfig."}</p>
                <ul style={{ margin: 0, padding: 0, listStyle: "none", display: "flex", flexDirection: "column", gap: "3px" }}>
                  <li style={{ fontSize: "12px", color: "var(--text-muted)" }}>• Uncomment kubeconfig mount in <code style={{ color: "var(--text-secondary)" }}>docker-compose.yml</code></li>
                  <li style={{ fontSize: "12px", color: "var(--text-muted)" }}>• Or run: <code style={{ color: "var(--text-secondary)" }}>kind create cluster --name=kubernetes-cluster</code></li>
                </ul>
              </div>
            </div>
          ) : (
            <p style={{ fontSize: "13px", color: "var(--text-muted)", textAlign: "center" }}>No clusters match your search</p>
          )}
        </div>
      ) : (
        <>
          <div style={{ display: "flex", flexDirection: "column", gap: "5px" }}>
            {paginated.map((cluster) => {
              const isSelected = selected === cluster.context;
              const health = healthMap[cluster.context];
              const tooltip = health
                ? `Last: ${new Date(health.lastSeen).toLocaleDateString()} · ${health.severity}`
                : "Never investigated";
              const meta = parseClusterContext(cluster.context);
              const badge = PROVIDER_BADGE[meta.provider];

              return (
                <div key={cluster.context} onClick={() => onSelect(cluster.context)}
                  title={cluster.context}
                  style={{
                    display: "flex",
                    alignItems: "center",
                    gap: "10px",
                    padding: "10px 12px",
                    borderRadius: "6px",
                    border: `1px solid ${isSelected ? "var(--primary)" : "var(--border)"}`,
                    background: isSelected ? "rgba(124,58,237,0.1)" : "var(--bg-surface-2)",
                    cursor: "pointer",
                    transition: "all 0.12s",
                  }}
                  onMouseEnter={(e) => { if (!isSelected) { (e.currentTarget as HTMLElement).style.borderColor = "rgba(124,58,237,0.5)"; (e.currentTarget as HTMLElement).style.background = "var(--bg-hover)"; } }}
                  onMouseLeave={(e) => { if (!isSelected) { (e.currentTarget as HTMLElement).style.borderColor = "var(--border)"; (e.currentTarget as HTMLElement).style.background = "var(--bg-surface-2)"; } }}
                >
                  {/* Radio */}
                  <div style={{ width: "16px", height: "16px", borderRadius: "50%", border: `2px solid ${isSelected ? "var(--primary)" : "var(--border)"}`, display: "flex", alignItems: "center", justifyContent: "center", flexShrink: 0, transition: "border-color 0.12s" }}>
                    {isSelected && <div style={{ width: "6px", height: "6px", borderRadius: "50%", background: "var(--primary)" }} />}
                  </div>

                  <div style={{ flex: 1, minWidth: 0 }}>
                    {/* Name row: health dot + clean display name + provider badge + active */}
                    <div style={{ display: "flex", alignItems: "center", gap: "6px" }}>
                      <HealthDot severity={health?.severity ?? null} tooltip={tooltip} />
                      <span style={{ fontSize: "13px", fontWeight: isSelected ? 600 : 400, color: isSelected ? "var(--text-primary)" : "var(--text-secondary)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", flex: 1, minWidth: 0 }}>
                        {meta.displayName}
                      </span>
                      <span style={{ fontSize: "10px", fontWeight: 600, color: badge.text, background: badge.bg, border: `1px solid ${badge.border}`, borderRadius: 3, padding: "1px 5px", flexShrink: 0 }}>
                        {badge.label}
                      </span>
                      {cluster.current && (
                        <span style={{ fontSize: "10px", fontWeight: 700, padding: "1px 5px", borderRadius: "3px", background: "rgba(124,58,237,0.15)", border: "1px solid rgba(124,58,237,0.3)", color: "var(--text-accent)", textTransform: "uppercase", letterSpacing: "0.04em", flexShrink: 0 }}>
                          active
                        </span>
                      )}
                    </div>
                    {/* Subtitle: region or server */}
                    <p style={{ fontSize: "11px", color: "var(--text-muted)", margin: "2px 0 0", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                      {meta.region ?? cluster.server ?? cluster.context}
                    </p>
                  </div>
                </div>
              );
            })}
          </div>

          {totalPages > 1 && (
            <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginTop: "10px", paddingTop: "10px", borderTop: "1px solid var(--border-subtle)" }}>
              <button onClick={() => setPage((p) => Math.max(1, p - 1))} disabled={currentPage === 1}
                style={{ fontSize: "12px", color: "var(--text-secondary)", background: "none", border: "none", cursor: currentPage === 1 ? "not-allowed" : "pointer", opacity: currentPage === 1 ? 0.3 : 1, padding: "4px 8px" }}>
                ← Previous
              </button>
              <span style={{ fontSize: "12px", color: "var(--text-muted)" }}>Page {currentPage} of {totalPages}</span>
              <button onClick={() => setPage((p) => Math.min(totalPages, p + 1))} disabled={currentPage === totalPages}
                style={{ fontSize: "12px", color: "var(--text-secondary)", background: "none", border: "none", cursor: currentPage === totalPages ? "not-allowed" : "pointer", opacity: currentPage === totalPages ? 0.3 : 1, padding: "4px 8px" }}>
                Next →
              </button>
            </div>
          )}
        </>
      )}
    </div>
  );
}
