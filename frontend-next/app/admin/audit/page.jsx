"use client";

import { useState, useEffect } from "react";
import { useAuth } from "@/hooks/use-auth";
import { Navbar } from "@/components/layout/navbar";
import { adminAPI, getErrorMessage } from "@/lib/api-client";

export default function AuditPage() {
  const { user, logout } = useAuth();
  const [entries, setEntries] = useState([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    adminAPI.getAuditLog().then(setEntries).catch(() => {}).finally(() => setLoading(false));
  }, []);

  if (loading) return <div style={{ display: "flex", justifyContent: "center", padding: 80 }}><div className="spinner" /></div>;

  return (
    <div className="page">
      <Navbar user={user} onLogout={logout} />
      <main className="page-content">
        <div className="page-header">
          <div>
            <p className="eyebrow-label">Admin</p>
            <h1 className="page-title">Audit Log</h1>
            <p className="page-subtitle">Security audit trail</p>
          </div>
        </div>
        <div className="card card-tight animate-fade-in">
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  {["Timestamp", "User", "Action", "Detail"].map((h) => (
                    <th key={h}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {entries.map((e) => (
                  <tr key={e.id}>
                    <td style={{ fontFamily: "var(--font-mono)", fontSize: "0.72rem", color: "var(--text-secondary)" }}>{new Date(e.timestamp).toLocaleString()}</td>
                    <td style={{ fontFamily: "var(--font-mono)", fontSize: "0.78rem" }}>{e.username || "—"}</td>
                    <td style={{ fontWeight: 500 }}>{e.action}</td>
                    <td style={{ color: "var(--text-muted)" }}>{e.detail || "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      </main>
    </div>
  );
}
