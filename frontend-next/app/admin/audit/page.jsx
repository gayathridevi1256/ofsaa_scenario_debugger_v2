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
        <h1 style={{ fontSize: "1.5rem", fontWeight: 700, marginBottom: 4 }}>Audit Log</h1>
        <p style={{ color: "var(--text-muted)", fontSize: "0.875rem", marginBottom: 32 }}>Security audit trail</p>
        <div className="card">
          <table style={{ width: "100%", borderCollapse: "collapse", fontSize: "0.82rem" }}>
            <thead>
              <tr style={{ borderBottom: "1px solid var(--border)" }}>
                {["Timestamp", "User", "Action", "Detail"].map((h) => (
                  <th key={h} style={{ textAlign: "left", padding: "8px 12px", color: "var(--text-muted)", fontWeight: 600, textTransform: "uppercase", fontSize: "0.7rem" }}>{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {entries.map((e) => (
                <tr key={e.id} style={{ borderBottom: "1px solid var(--border)" }}>
                  <td style={{ padding: "10px 12px", fontFamily: "var(--font-mono)", fontSize: "0.72rem" }}>{new Date(e.timestamp).toLocaleString()}</td>
                  <td style={{ padding: "10px 12px", fontFamily: "var(--font-mono)" }}>{e.username || "—"}</td>
                  <td style={{ padding: "10px 12px" }}>{e.action}</td>
                  <td style={{ padding: "10px 12px", color: "var(--text-muted)" }}>{e.detail || "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </main>
    </div>
  );
}
