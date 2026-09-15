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
        <div className="page-header">
          <div>
            <p className="eyebrow-label">Batch</p>
            <h1 className="page-title">Batch Results</h1>
            <p className="page-subtitle">Batch run summary</p>
          </div>
        </div>
        <div className="card" style={{ textAlign: "center", padding: 60, color: "var(--text-muted)" }}>
          Batch results page — upload multiple files to see results here
        </div>
      </main>
    </div>
  );
}
