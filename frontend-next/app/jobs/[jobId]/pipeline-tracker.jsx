"use client";

const STATUS_CFG = {
  idle: { bg: "var(--bg-overlay)", color: "var(--text-muted)", border: "var(--border)", label: "Pending", icon: "○" },
  running: { bg: "var(--accent-subtle)", color: "var(--accent)", border: "var(--accent)", label: "Running", icon: "↻" },
  completed: { bg: "var(--success-dim)", color: "var(--success)", border: "var(--success)", label: "Done", icon: "✓" },
  failed: { bg: "var(--danger-dim)", color: "var(--danger)", border: "var(--danger)", label: "Failed", icon: "✕" },
};

export function PipelineTracker({ steps, states, outputs, wsStatus }) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
      {steps.map((step, idx) => {
        const status = states[step.key] || "idle";
        const cfg = STATUS_CFG[status] || STATUS_CFG.idle;
        const output = outputs[step.key];
        return (
          <div key={step.key} className="card" style={{
            display: "flex", alignItems: "center", gap: 16, padding: 16,
            borderLeft: `4px solid ${cfg.border}`,
            background: status === "running" ? "var(--accent-subtle)" : status === "completed" ? "var(--success-dim)" : status === "failed" ? "var(--danger-dim)" : undefined,
          }}>
            <div style={{
              width: 36, height: 36, borderRadius: "50%", display: "flex", alignItems: "center", justifyContent: "center",
              background: cfg.bg, color: cfg.color, border: `2px solid ${cfg.border}`, fontWeight: 700, fontSize: "0.9rem",
            }}>
              {status === "running" ? <div className="spinner spinner-sm" /> : cfg.icon}
            </div>
            <div style={{ flex: 1, minWidth: 0 }}>
              <div style={{ fontSize: "0.7rem", color: "var(--text-muted)" }}>0{idx + 1}</div>
              <div style={{ fontWeight: 600, fontSize: "0.875rem", color: cfg.color }}>{step.label}</div>
              <div style={{ fontSize: "0.75rem", color: "var(--text-muted)" }}>{step.desc}</div>
              {output && (
                <pre style={{ marginTop: 6, fontSize: "0.72rem", fontFamily: "var(--font-mono)", color: "var(--text-secondary)", whiteSpace: "pre-wrap", maxHeight: 120, overflow: "auto" }}>
                  {output}
                </pre>
              )}
            </div>
            <span className={`badge ${cfg.label === "Done" ? "badge-success" : cfg.label === "Failed" ? "badge-danger" : cfg.label === "Running" ? "badge-warning" : "badge-muted"}`}>
              {cfg.label}
            </span>
          </div>
        );
      })}
    </div>
  );
}
