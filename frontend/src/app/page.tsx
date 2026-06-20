"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { insforge } from "@/lib/insforge";

export default function RootPage() {
  const router = useRouter();

  useEffect(() => {
    insforge.auth.getCurrentUser().then(({ data }) => {
      if (data?.user) {
        router.replace("/dashboard");
      } else {
        router.replace("/login");
      }
    });
  }, [router]);

  return (
    <div className="min-h-screen bg-gray-950 flex items-center justify-center">
      <div className="w-6 h-6 border-2 border-indigo-500 border-t-transparent rounded-full animate-spin" />
    </div>
  );
}
