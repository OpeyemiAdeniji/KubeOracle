"use client";

import { createContext, useContext, useState, useEffect, useRef } from "react";
import { useRouter } from "next/navigation";
import { insforge } from "@/lib/insforge";
import Sidebar from "@/components/Sidebar";
import Topbar from "@/components/Topbar";

export const UserContext = createContext<any>(null);
export function useUser() { return useContext(UserContext); }

export default function DashboardLayout({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const [user, setUser] = useState<any>(null);
  const [sidebarOpen, setSidebarOpen] = useState(true);
  const mountedRef = useRef(true);

  useEffect(() => {
    mountedRef.current = true;
    insforge.auth.getCurrentUser().then(({ data }) => {
      if (!mountedRef.current) return;
      if (!data?.user) { router.push("/login"); return; }
      setUser(data.user);
    });
    return () => { mountedRef.current = false; };
  }, [router]);

  const handleSignOut = async () => {
    await insforge.auth.signOut();
    router.push("/login");
  };

  if (!user) {
    return (
      <div style={{ minHeight: "100vh", background: "var(--bg-primary)", display: "flex", alignItems: "center", justifyContent: "center" }}>
        <div className="w-6 h-6 border-2 rounded-full animate-spin"
          style={{ borderColor: "var(--border)", borderTopColor: "var(--primary)" }} />
      </div>
    );
  }

  return (
    <UserContext.Provider value={user}>
      <div style={{ display: "flex", height: "100vh", overflow: "hidden", background: "var(--bg-primary)" }}>
        <Sidebar open={sidebarOpen} />
        <div style={{ flex: 1, display: "flex", flexDirection: "column", overflow: "hidden", minWidth: 0 }}>
          <Topbar
            user={user}
            sidebarOpen={sidebarOpen}
            onToggleSidebar={() => setSidebarOpen((v) => !v)}
            onSignOut={handleSignOut}
          />
          <main style={{ flex: 1, overflowY: "auto", padding: "24px 28px" }}>
            {children}
          </main>
        </div>
      </div>
    </UserContext.Provider>
  );
}
