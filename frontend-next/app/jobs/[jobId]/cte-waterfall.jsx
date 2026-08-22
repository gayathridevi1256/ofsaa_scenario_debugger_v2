"use client";

import { useState } from "react";

export function CteWaterfall({ cteResults }) {
  if (!cteResults?.length) return null;

  return (
    <div style={{ marginTop: 32 }}>
      <h2 style={{ fontSize: "0.85rem", fontWeight: 700, color: "var(--text-muted)", textTransform: "uppercase", letterSpacing: "0.05em", marginBottom: 12 }}>
        CTE Waterfall
      </h2>
      <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
        {cteResults.map((cte, idx) => (
          <CteRow key={cte.name} cte={cte} index={idx} isLast={idx === cteResults.length - 1} />
        ))}
      </div>
    </div>
  );
}

function CteRow({ cte, index, isLast }) {
  const [expanded, setExpanded] = useState(false);
  const isOk = cte.status === "ok";

  return (
    <div style={{ position: "relative" }}>
      {!isLast && <div style={{ position: "absolute", left: 18, top: 40, bottom: -8, width: 2, background: isOk ? "var(--success)" : "var(--danger)", opacity: 0.3 }} />}
      <div
        onClick={() => setExpanded(!expanded)}
        style={{
          display: "flex", alignItems: "center", gap: 12, padding: 12,
          background: "var(--bg-surface)", border: `1px solid ${isOk ? "var(--success)" : "var(--danger)"}`,
          borderRadius: "var(--radius-md)", cursor: "pointer",
        }}
      >
        <div style={{
          width: 12, height: 12, borderRadius: "50%",
          background: isOk ? "var(--success)" : "var(--danger)",
          boxShadow: isOk ? "0 0 8px rgba(16,185,129,0.4)" : "0 0 12px rgba(239,68,68,0.6)",
        }} />
        <span style={{ fontFamily: "var(--font-mono)", fontSize: "0.7rem", color: "var(--text-muted)" }}>CTE {index + 1}</span>
        <span style={{ fontWeight: 600, fontFamily: "var(--font-mono)", color: isOk ? "var(--success)" : "var(--danger)" }}>{cte.name}</span>
        <span style={{ marginLeft: "auto", fontFamily: "var(--font-mono)", fontSize: "0.85rem", fontWeight: 700, color: isOk ? "var(--success)" : "var(--danger)" }}>
          {cte.rows?.toLocaleString() ?? "—"}
        </span>
        <span className={`badge ${isOk ? "badge-success" : "badge-danger"}`}>{isOk ? "PASS" : "EMPTY"}</span>
      </div>
    </div>
  );
}
