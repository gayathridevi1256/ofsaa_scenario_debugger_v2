"use client";

import { useState } from "react";
import { useAuth } from "@/hooks/use-auth";
import { Navbar } from "@/components/layout/navbar";

export default function BatchPage() {
  const { user, logout } = useAuth();
  return (
    <div className="page">
      <Navbar user={user} onLogout={logout} />
      <main className="page-content">
        <h1 style={{ fontSize: "1.5rem", fontWeight: 700, marginBottom: 4 }}>Batch Results</h1>
        <p style={{ color: "var(--text-muted)", fontSize: "0.875rem", marginBottom: 32 }}>Batch run summary</p>
        <div className="card" style={{ textAlign: "center", padding: 60, color: "var(--text-muted)" }}>
          Batch results page — upload multiple files to see results here
        </div>
      </main>
    </div>
  );
}
