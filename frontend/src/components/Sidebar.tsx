"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

interface SidebarProps {
  open: boolean;
}

const NAV_ITEMS = [
  {
    href: "/dashboard",
    label: "Overview",
    icon: (
      <svg width="16" height="16" fill="none" viewBox="0 0 24 24" stroke="currentColor">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
          d="M4 6a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2H6a2 2 0 01-2-2V6zm10 0a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2h-2a2 2 0 01-2-2V6zM4 16a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2H6a2 2 0 01-2-2v-2zm10 0a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2h-2a2 2 0 01-2-2v-2z" />
      </svg>
    ),
    exact: true,
  },
  {
    href: "/dashboard/query",
    label: "Query",
    icon: (
      <svg width="16" height="16" fill="none" viewBox="0 0 24 24" stroke="currentColor">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
          d="M8 12h.01M12 12h.01M16 12h.01M21 12c0 4.418-4.03 8-9 8a9.863 9.863 0 01-4.255-.949L3 20l1.395-3.72C3.512 15.042 3 13.574 3 12c0-4.418 4.03-8 9-8s9 3.582 9 8z" />
      </svg>
    ),
  },
  {
    href: "/dashboard/investigate",
    label: "Investigate",
    icon: (
      <svg width="16" height="16" fill="none" viewBox="0 0 24 24" stroke="currentColor">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
          d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2m-3 7h3m-3 4h3m-6-4h.01M9 16h.01" />
      </svg>
    ),
  },
  {
    href: "/dashboard/history",
    label: "History",
    icon: (
      <svg width="16" height="16" fill="none" viewBox="0 0 24 24" stroke="currentColor">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
          d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z" />
      </svg>
    ),
  },
];

const BOTTOM_ITEMS = [
  {
    href: "/dashboard/settings",
    label: "Settings",
    icon: (
      <svg width="16" height="16" fill="none" viewBox="0 0 24 24" stroke="currentColor">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
          d="M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.065 2.572c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.572 1.065c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.065-2.572c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z" />
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" />
      </svg>
    ),
  },
];

function NavItem({
  href, label, icon, exact,
}: {
  href: string; label: string; icon: React.ReactNode; exact?: boolean;
}) {
  const pathname = usePathname();
  const isActive = exact ? pathname === href : pathname.startsWith(href);

  return (
    <Link href={href} style={{ textDecoration: "none" }}>
      <div style={{
        display: "flex",
        alignItems: "center",
        gap: "10px",
        padding: "8px 12px",
        borderRadius: "6px",
        fontSize: "13px",
        fontWeight: isActive ? 600 : 400,
        color: isActive ? "var(--text-accent)" : "var(--text-secondary)",
        background: isActive ? "rgba(124, 58, 237, 0.12)" : "transparent",
        borderLeft: isActive ? "2px solid var(--primary)" : "2px solid transparent",
        cursor: "pointer",
        transition: "all 0.12s ease",
        marginLeft: "-2px",
      }}
        onMouseEnter={(e) => {
          if (!isActive) {
            (e.currentTarget as HTMLElement).style.background = "var(--bg-hover)";
            (e.currentTarget as HTMLElement).style.color = "var(--text-primary)";
          }
        }}
        onMouseLeave={(e) => {
          if (!isActive) {
            (e.currentTarget as HTMLElement).style.background = "transparent";
            (e.currentTarget as HTMLElement).style.color = "var(--text-secondary)";
          }
        }}
      >
        <span style={{ opacity: isActive ? 1 : 0.7, flexShrink: 0 }}>{icon}</span>
        <span>{label}</span>
      </div>
    </Link>
  );
}

export default function Sidebar({ open }: SidebarProps) {
  if (!open) return null;

  return (
    <aside style={{
      width: "220px",
      flexShrink: 0,
      height: "100vh",
      background: "var(--bg-surface)",
      borderRight: "1px solid var(--border)",
      display: "flex",
      flexDirection: "column",
      overflow: "hidden",
    }}>
      {/* Logo */}
      <div style={{ padding: "0 16px", height: "56px", display: "flex", alignItems: "center", borderBottom: "1px solid var(--border-subtle)", flexShrink: 0 }}>
        <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
          <div style={{ width: "28px", height: "28px", borderRadius: "6px", background: "var(--primary)", display: "flex", alignItems: "center", justifyContent: "center", flexShrink: 0 }}>
            <svg width="16" height="16" fill="none" viewBox="0 0 24 24" stroke="white">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.8}
                d="M9 3H5a2 2 0 00-2 2v4m6-6h10a2 2 0 012 2v4M9 3v18m0 0h10a2 2 0 002-2V9M9 21H5a2 2 0 01-2-2V9m0 0h18" />
            </svg>
          </div>
          <span style={{ fontSize: "15px", fontWeight: 700, color: "var(--text-primary)" }}>KubeOracle</span>
        </div>
      </div>

      {/* Nav section */}
      <div style={{ flex: 1, overflowY: "auto", padding: "12px 14px" }}>
        <p style={{ fontSize: "10px", fontWeight: 600, color: "var(--text-muted)", textTransform: "uppercase", letterSpacing: "0.08em", padding: "0 12px", marginBottom: "6px" }}>
          Navigation
        </p>
        <nav style={{ display: "flex", flexDirection: "column", gap: "2px" }}>
          {NAV_ITEMS.map((item) => (
            <NavItem key={item.href} {...item} />
          ))}
        </nav>
      </div>

      {/* Bottom section */}
      <div style={{ padding: "12px 14px", borderTop: "1px solid var(--border-subtle)" }}>
        <p style={{ fontSize: "10px", fontWeight: 600, color: "var(--text-muted)", textTransform: "uppercase", letterSpacing: "0.08em", padding: "0 12px", marginBottom: "6px" }}>
          Account
        </p>
        <nav style={{ display: "flex", flexDirection: "column", gap: "2px" }}>
          {BOTTOM_ITEMS.map((item) => (
            <NavItem key={item.href} {...item} />
          ))}
        </nav>
      </div>
    </aside>
  );
}
