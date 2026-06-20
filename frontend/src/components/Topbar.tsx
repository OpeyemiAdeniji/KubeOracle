"use client";

import { useState, useEffect, useRef, useCallback, KeyboardEvent } from "react";
import { useRouter } from "next/navigation";
import axios from "axios";
import { Cluster } from "@/types";
import { parseClusterContext, PROVIDER_BADGE } from "@/lib/clusterUtils";

const API_BASE =
  typeof window !== "undefined"
    ? process.env.NEXT_PUBLIC_API_BASE_URL || "http://localhost:8000"
    : "http://localhost:8000";

interface TopbarProps {
  user: any;
  sidebarOpen: boolean;
  onToggleSidebar: () => void;
  onSignOut: () => void;
}

// ── Shared dropdown shell ─────────────────────────────────────────────────────
function DropdownShell({
  children,
  right = 0,
  width = 240,
  onClose,
}: {
  children: React.ReactNode;
  right?: number;
  width?: number;
  onClose: () => void;
}) {
  return (
    <>
      <div style={{ position: "fixed", inset: 0, zIndex: 29 }} onClick={onClose} />
      <div style={{
        position: "absolute",
        top: "calc(100% + 8px)",
        right,
        zIndex: 30,
        width,
        background: "var(--bg-surface)",
        border: "1px solid var(--border-subtle)",
        borderRadius: 8,
        boxShadow: "0 12px 32px rgba(0,0,0,0.5)",
        overflow: "hidden",
      }}>
        {children}
      </div>
    </>
  );
}

// ── Cluster search ─────────────────────────────────────────────────────────────
function ClusterSearch() {
  const router = useRouter();
  const [query, setQuery] = useState("");
  const [open, setOpen] = useState(false);
  const [clusters, setClusters] = useState<Cluster[]>([]);
  const [loading, setLoading] = useState(false);
  const [activeIdx, setActiveIdx] = useState(-1);
  const inputRef = useRef<HTMLInputElement>(null);
  const loadedRef = useRef(false);

  // Prefetch clusters once on mount
  useEffect(() => {
    if (loadedRef.current) return;
    loadedRef.current = true;
    setLoading(true);
    axios.get(`${API_BASE}/clusters`, { timeout: 10_000 })
      .then(({ data }) => setClusters(data.clusters || []))
      .catch(() => {})
      .finally(() => setLoading(false));
  }, []);

  const filtered = query.trim()
    ? clusters.filter((c) => {
        const { displayName, region } = parseClusterContext(c.context);
        const q = query.trim().toLowerCase();
        return (
          displayName.toLowerCase().includes(q) ||
          (region && region.toLowerCase().includes(q)) ||
          c.context.toLowerCase().includes(q)
        );
      })
    : clusters;

  const navigate = useCallback((ctx: string) => {
    setOpen(false);
    setQuery("");
    setActiveIdx(-1);
    router.push(`/dashboard/investigate?context=${encodeURIComponent(ctx)}`);
  }, [router]);

  const handleKeyDown = (e: KeyboardEvent<HTMLInputElement>) => {
    if (!open) return;
    if (e.key === "Escape") { setOpen(false); setActiveIdx(-1); inputRef.current?.blur(); return; }
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setActiveIdx((i) => Math.min(i + 1, filtered.length - 1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setActiveIdx((i) => Math.max(i - 1, -1));
    } else if (e.key === "Enter") {
      if (activeIdx >= 0 && filtered[activeIdx]) navigate(filtered[activeIdx].context);
    }
  };

  const showDropdown = open && (loading || filtered.length > 0 || query.trim().length > 0);

  return (
    <div style={{ flex: 1, maxWidth: 360, position: "relative" }}>
      {/* Input */}
      <svg style={{ position: "absolute", left: 10, top: "50%", transform: "translateY(-50%)", pointerEvents: "none", color: "var(--text-muted)" }}
        width="13" height="13" fill="none" viewBox="0 0 24 24" stroke="currentColor">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
      </svg>
      <input
        ref={inputRef}
        type="text"
        value={query}
        onChange={(e) => { setQuery(e.target.value); setActiveIdx(-1); if (!open) setOpen(true); }}
        onFocus={() => setOpen(true)}
        onKeyDown={handleKeyDown}
        placeholder="Search clusters…"
        style={{
          width: "100%",
          background: "var(--bg-surface-2)",
          border: `1px solid ${open ? "var(--primary)" : "var(--border)"}`,
          borderRadius: 6,
          padding: "6px 28px 6px 30px",
          fontSize: 13,
          color: "var(--text-primary)",
          outline: "none",
          transition: "border-color 0.15s",
        }}
      />
      {query && (
        <button
          onMouseDown={(e) => { e.preventDefault(); setQuery(""); setActiveIdx(-1); inputRef.current?.focus(); }}
          style={{ position: "absolute", right: 8, top: "50%", transform: "translateY(-50%)", background: "none", border: "none", color: "var(--text-muted)", cursor: "pointer", display: "flex", padding: 2 }}>
          <svg width="12" height="12" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
          </svg>
        </button>
      )}

      {/* Dropdown */}
      {showDropdown && (
        <>
          {/* Close on outside click — but NOT on the input itself */}
          <div style={{ position: "fixed", inset: 0, zIndex: 29 }} onMouseDown={() => { setOpen(false); setActiveIdx(-1); }} />
          <div style={{
            position: "absolute",
            top: "calc(100% + 6px)",
            left: 0,
            right: 0,
            zIndex: 30,
            background: "var(--bg-surface)",
            border: "1px solid var(--border-subtle)",
            borderRadius: 8,
            boxShadow: "0 12px 32px rgba(0,0,0,0.5)",
            overflow: "hidden",
            maxHeight: 280,
            overflowY: "auto",
          }}>
            {loading && clusters.length === 0 ? (
              <div style={{ padding: "12px 14px", display: "flex", alignItems: "center", gap: 8 }}>
                <div className="animate-spin" style={{ width: 12, height: 12, border: "2px solid var(--border)", borderTopColor: "var(--primary)", borderRadius: "50%" }} />
                <span style={{ fontSize: 12, color: "var(--text-muted)" }}>Loading clusters…</span>
              </div>
            ) : filtered.length === 0 ? (
              <div style={{ padding: "12px 14px" }}>
                <span style={{ fontSize: 12, color: "var(--text-muted)" }}>
                  {query ? `No clusters matching "${query}"` : "No clusters found"}
                </span>
              </div>
            ) : (
              filtered.map((cluster, idx) => {
                const meta = parseClusterContext(cluster.context);
                const badge = PROVIDER_BADGE[meta.provider];
                const isActive = idx === activeIdx;
                return (
                  <button
                    key={cluster.context}
                    onMouseDown={(e) => { e.preventDefault(); navigate(cluster.context); }}
                    onMouseEnter={() => setActiveIdx(idx)}
                    style={{
                      width: "100%",
                      display: "flex",
                      alignItems: "center",
                      gap: 10,
                      padding: "9px 14px",
                      background: isActive ? "var(--bg-hover)" : "transparent",
                      border: "none",
                      borderBottom: idx < filtered.length - 1 ? "1px solid var(--border)" : "none",
                      cursor: "pointer",
                      textAlign: "left",
                      transition: "background 0.1s",
                    }}
                  >
                    <div style={{ flex: 1, minWidth: 0 }}>
                      <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
                        <span style={{ fontSize: 13, fontWeight: 500, color: "var(--text-primary)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                          {meta.displayName}
                        </span>
                        {cluster.current && (
                          <span style={{ fontSize: 9, fontWeight: 700, color: "var(--text-accent)", background: "rgba(124,58,237,0.18)", border: "1px solid rgba(124,58,237,0.35)", borderRadius: 3, padding: "1px 4px", flexShrink: 0, textTransform: "uppercase" }}>
                            active
                          </span>
                        )}
                      </div>
                      {meta.region && (
                        <p style={{ fontSize: 11, color: "var(--text-muted)", margin: "1px 0 0" }}>{meta.region}</p>
                      )}
                    </div>
                    <span style={{ fontSize: 10, fontWeight: 600, color: badge.text, background: badge.bg, border: `1px solid ${badge.border}`, borderRadius: 3, padding: "1px 6px", flexShrink: 0 }}>
                      {badge.label}
                    </span>
                    <svg width="12" height="12" fill="none" viewBox="0 0 24 24" stroke="currentColor" style={{ color: "var(--text-muted)", flexShrink: 0 }}>
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" />
                    </svg>
                  </button>
                );
              })
            )}
          </div>
        </>
      )}
    </div>
  );
}

// ── Notification bell ──────────────────────────────────────────────────────────
function NotificationBell() {
  const [open, setOpen] = useState(false);
  const count = 0; // future: wire to real alerts

  return (
    <div style={{ position: "relative" }}>
      <button
        onClick={() => setOpen((v) => !v)}
        title="Notifications"
        style={{
          position: "relative",
          padding: 6,
          borderRadius: 5,
          background: "none",
          border: "none",
          color: open ? "var(--text-primary)" : "var(--text-muted)",
          cursor: "pointer",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          transition: "color 0.15s",
        }}
        onMouseEnter={(e) => { (e.currentTarget as HTMLElement).style.color = "var(--text-primary)"; (e.currentTarget as HTMLElement).style.background = "var(--bg-hover)"; }}
        onMouseLeave={(e) => { if (!open) { (e.currentTarget as HTMLElement).style.color = "var(--text-muted)"; (e.currentTarget as HTMLElement).style.background = "none"; } }}
      >
        <svg width="18" height="18" fill="none" viewBox="0 0 24 24" stroke="currentColor">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
            d="M15 17h5l-1.405-1.405A2.032 2.032 0 0118 14.158V11a6.002 6.002 0 00-4-5.659V5a2 2 0 10-4 0v.341C7.67 6.165 6 8.388 6 11v3.159c0 .538-.214 1.055-.595 1.436L4 17h5m6 0v1a3 3 0 11-6 0v-1m6 0H9" />
        </svg>
        {count > 0 && (
          <span style={{
            position: "absolute", top: 2, right: 2,
            width: 16, height: 16, borderRadius: "50%",
            background: "#dc2626", color: "#fff",
            fontSize: 9, fontWeight: 700,
            display: "flex", alignItems: "center", justifyContent: "center",
            border: "2px solid var(--bg-surface)",
          }}>
            {count > 9 ? "9+" : count}
          </span>
        )}
      </button>

      {open && (
        <DropdownShell width={280} right={0} onClose={() => setOpen(false)}>
          {/* Header */}
          <div style={{ padding: "12px 14px 10px", borderBottom: "1px solid var(--border)" }}>
            <p style={{ fontSize: 13, fontWeight: 600, color: "var(--text-primary)", margin: 0 }}>Notifications</p>
          </div>

          {/* Empty state */}
          <div style={{ padding: "28px 14px", display: "flex", flexDirection: "column", alignItems: "center", gap: 10 }}>
            <div style={{ width: 36, height: 36, borderRadius: 8, background: "var(--bg-surface-2)", border: "1px solid var(--border)", display: "flex", alignItems: "center", justifyContent: "center" }}>
              <svg width="18" height="18" fill="none" viewBox="0 0 24 24" stroke="currentColor" style={{ color: "var(--text-muted)" }}>
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.75}
                  d="M15 17h5l-1.405-1.405A2.032 2.032 0 0118 14.158V11a6.002 6.002 0 00-4-5.659V5a2 2 0 10-4 0v.341C7.67 6.165 6 8.388 6 11v3.159c0 .538-.214 1.055-.595 1.436L4 17h5m6 0v1a3 3 0 11-6 0v-1m6 0H9" />
              </svg>
            </div>
            <div style={{ textAlign: "center" }}>
              <p style={{ fontSize: 13, fontWeight: 500, color: "var(--text-secondary)", margin: "0 0 3px" }}>No notifications</p>
              <p style={{ fontSize: 11, color: "var(--text-muted)", margin: 0 }}>
                Critical investigation alerts will appear here
              </p>
            </div>
          </div>
        </DropdownShell>
      )}
    </div>
  );
}

// ── User avatar dropdown ───────────────────────────────────────────────────────
function UserDropdown({ user, onSignOut }: { user: any; onSignOut: () => void }) {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const email: string = user?.email ?? "";
  const initials = email ? email[0].toUpperCase() : "U";

  const menuItem = (onClick: () => void, label: string, icon: React.ReactNode, danger = false) => (
    <button
      onClick={onClick}
      style={{
        width: "100%", display: "flex", alignItems: "center", gap: 8,
        padding: "8px 12px", background: "none", border: "none", borderRadius: 5,
        color: danger ? "#f87171" : "var(--text-secondary)", fontSize: 13,
        cursor: "pointer", textAlign: "left", transition: "background 0.12s, color 0.12s",
      }}
      onMouseEnter={(e) => {
        (e.currentTarget as HTMLElement).style.background = danger ? "rgba(239,68,68,0.1)" : "var(--bg-hover)";
        (e.currentTarget as HTMLElement).style.color = danger ? "#f87171" : "var(--text-primary)";
      }}
      onMouseLeave={(e) => {
        (e.currentTarget as HTMLElement).style.background = "none";
        (e.currentTarget as HTMLElement).style.color = danger ? "#f87171" : "var(--text-secondary)";
      }}
    >
      {icon}
      {label}
    </button>
  );

  return (
    <div style={{ position: "relative" }}>
      <button
        onClick={() => setOpen((v) => !v)}
        style={{
          display: "flex", alignItems: "center", gap: 8, padding: "4px 8px",
          borderRadius: 6, background: "none", border: "none", cursor: "pointer",
          transition: "background 0.15s",
        }}
        onMouseEnter={(e) => ((e.currentTarget as HTMLElement).style.background = "var(--bg-hover)")}
        onMouseLeave={(e) => ((e.currentTarget as HTMLElement).style.background = "none")}
      >
        <div style={{
          width: 28, height: 28, borderRadius: "50%", background: "var(--primary)",
          display: "flex", alignItems: "center", justifyContent: "center",
          fontSize: 12, fontWeight: 700, color: "#fff", flexShrink: 0,
        }}>
          {initials}
        </div>
        <span style={{ fontSize: 13, color: "var(--text-secondary)", maxWidth: 140, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
          {email}
        </span>
        <svg width="12" height="12" fill="none" viewBox="0 0 24 24" stroke="currentColor" style={{ color: "var(--text-muted)", flexShrink: 0 }}>
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
        </svg>
      </button>

      {open && (
        <DropdownShell width={200} right={0} onClose={() => setOpen(false)}>
          {/* Email header */}
          <div style={{ padding: "10px 12px 8px", borderBottom: "1px solid var(--border)" }}>
            <p style={{ fontSize: 11, color: "var(--text-muted)", margin: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
              {email}
            </p>
          </div>

          {/* Menu items */}
          <div style={{ padding: "4px" }}>
            {menuItem(
              () => { setOpen(false); router.push("/dashboard/settings"); },
              "Settings",
              <svg width="14" height="14" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
                  d="M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.065 2.572c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.572 1.065c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.065-2.572c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z" />
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" />
              </svg>
            )}

            <div style={{ height: 1, background: "var(--border)", margin: "4px 0" }} />

            {menuItem(
              () => { setOpen(false); onSignOut(); },
              "Sign Out",
              <svg width="14" height="14" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
                  d="M17 16l4-4m0 0l-4-4m4 4H7m6 4v1a3 3 0 01-3 3H6a3 3 0 01-3-3V7a3 3 0 013-3h4a3 3 0 013 3v1" />
              </svg>,
              true
            )}
          </div>
        </DropdownShell>
      )}
    </div>
  );
}

// ── Topbar ────────────────────────────────────────────────────────────────────
export default function Topbar({ user, sidebarOpen, onToggleSidebar, onSignOut }: TopbarProps) {
  return (
    <header style={{
      height: 56,
      flexShrink: 0,
      background: "var(--bg-surface)",
      borderBottom: "1px solid var(--border)",
      display: "flex",
      alignItems: "center",
      padding: "0 20px",
      gap: 12,
      position: "relative",
      zIndex: 10,
    }}>
      {/* Hamburger */}
      <button
        onClick={onToggleSidebar}
        title={sidebarOpen ? "Collapse sidebar" : "Expand sidebar"}
        style={{ padding: 6, borderRadius: 5, background: "none", border: "none", color: "var(--text-muted)", cursor: "pointer", display: "flex", alignItems: "center", justifyContent: "center", flexShrink: 0, transition: "color 0.15s, background 0.15s" }}
        onMouseEnter={(e) => { (e.currentTarget as HTMLElement).style.color = "var(--text-primary)"; (e.currentTarget as HTMLElement).style.background = "var(--bg-hover)"; }}
        onMouseLeave={(e) => { (e.currentTarget as HTMLElement).style.color = "var(--text-muted)"; (e.currentTarget as HTMLElement).style.background = "none"; }}
      >
        <svg width="18" height="18" fill="none" viewBox="0 0 24 24" stroke="currentColor">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 6h16M4 12h16M4 18h16" />
        </svg>
      </button>

      {/* Cluster search */}
      <ClusterSearch />

      {/* Spacer */}
      <div style={{ flex: 1 }} />

      {/* Bell */}
      <NotificationBell />

      {/* User */}
      <UserDropdown user={user} onSignOut={onSignOut} />
    </header>
  );
}
