"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";

const FAILURE_TYPE_COLORS = {
  where_combination: "var(--warning, #f59e0b)",
  where_condition: "var(--danger)",
  having: "var(--danger)",
  join: "var(--accent)",
  threshold_kill: "var(--warning, #f59e0b)",
  upstream_dependency: "var(--text-muted)",
  no_source_data: "var(--text-muted)",
  unknown: "var(--text-muted)",
};

export function RootCauseCard({ rootCause, results, aiRecommendation, logFilePath }) {
  const router = useRouter();
  if (!results?.length && !rootCause) return null;

  // Two diagnosis paths can identify a tunable threshold: the specialized
  // THRESHOLD_KILL detector (aggregated/inner-query conditions tied to
  // KDD_TSHLD config) and the generic WHERE/HAVING elimination path, which
  // also populates threshold_suggestions whenever the killer condition is a
  // plain numeric range comparison — but keeps failure_type as "WHERE"/
  // "HAVING" rather than "threshold_kill". Checking threshold_suggestions
  // directly catches both, so the banner shows whenever there's a concrete,
  // actionable suggestion to act on — not just for the narrower path.
  const hasThresholdKill = results?.some((r) =>
    (r.failure_type || "").toLowerCase() === "threshold_kill" ||
    r.threshold_suggestions?.length > 0
  );

  return (
    <div className="card" style={{ marginTop: 24, borderLeft: "4px solid var(--danger)" }}>
      <h2 style={{ fontSize: "1rem", fontWeight: 700, marginBottom: 12, display: "flex", alignItems: "center", gap: 8 }}>
        <span style={{ color: "var(--danger)" }}>⚠</span> Root Cause Analysis
      </h2>
      {hasThresholdKill && logFilePath && (
        <div
          style={{
            marginBottom: 16, padding: 12, borderRadius: "var(--radius-sm)",
            border: "1px solid var(--warning, #f59e0b)", background: "rgba(245,158,11,0.08)",
            display: "flex", alignItems: "center", justifyContent: "space-between", gap: 12, flexWrap: "wrap",
          }}
        >
          <div style={{ fontSize: "0.8rem" }}>
            <strong>This scenario's thresholds are killing all the rows.</strong> Tune them against real data from the same log — no need to re-upload.
          </div>
          <button
            className="btn btn-primary btn-sm"
            onClick={() => router.push(`/threshold-tuning?file_path=${encodeURIComponent(logFilePath)}`)}
          >
            🎯 Tune These Thresholds
          </button>
        </div>
      )}
      {aiRecommendation && (
        <div
          style={{
            marginBottom: 16,
            padding: 12,
            borderRadius: "var(--radius-sm)",
            border: "1px solid var(--accent)",
            background: "var(--accent-dim, rgba(59,130,246,0.08))",
          }}
        >
          <div style={{ display: "flex", alignItems: "center", gap: 6, marginBottom: 4 }}>
            <span>💡</span>
            <span style={{ fontSize: "0.72rem", fontWeight: 700, color: "var(--accent)", textTransform: "uppercase" }}>
              AI Recommendation
            </span>
          </div>
          <div style={{ fontSize: "0.85rem", whiteSpace: "pre-wrap", lineHeight: 1.5 }}>{aiRecommendation}</div>
        </div>
      )}
      {results?.length > 0 ? (
        <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
          {results.map((r, i) => (
            <CteDiagnosisCard key={i} result={r} isPrimary={i === 0} />
          ))}
        </div>
      ) : rootCause ? (
        <div style={{ fontSize: "0.875rem", fontFamily: "var(--font-mono)", whiteSpace: "pre-wrap" }}>{rootCause}</div>
      ) : null}
    </div>
  );
}

function CteDiagnosisCard({ result, isPrimary }) {
  const [expanded, setExpanded] = useState(isPrimary);
  const ft = (result.failure_type || "unknown").toLowerCase();
  const color = FAILURE_TYPE_COLORS[ft] || "var(--danger)";
  const isUpstream = ft === "upstream_dependency";

  return (
    <div
      style={{
        padding: 12,
        background: isPrimary ? "var(--danger-dim, rgba(239,68,68,0.08))" : "var(--bg-elevated)",
        borderRadius: "var(--radius-sm)",
        border: isPrimary ? `1px solid ${color}` : "1px solid var(--border)",
      }}
    >
      {/* Header row: CTE name + failure type + expand toggle */}
      <div
        onClick={() => setExpanded(!expanded)}
        style={{ display: "flex", alignItems: "center", gap: 8, cursor: "pointer", marginBottom: expanded ? 12 : 0 }}
      >
        <span style={{
          width: 10, height: 10, borderRadius: "50%",
          background: color, flexShrink: 0,
        }} />
        <span style={{ fontWeight: 700, fontFamily: "var(--font-mono)", fontSize: "0.85rem" }}>
          {result.cte_name}
        </span>
        <span style={{ fontSize: "0.7rem", color, fontWeight: 600, textTransform: "uppercase" }}>
          {result.failure_type || "unknown"}
        </span>
        {isUpstream && result.upstream_cte && (
          <span style={{ fontSize: "0.7rem", color: "var(--text-muted)" }}>
            ← depends on {result.upstream_cte}
          </span>
        )}
        <span style={{ marginLeft: "auto", fontSize: "0.7rem", color: "var(--text-muted)" }}>
          {expanded ? "▼" : "▶"}
        </span>
      </div>

      {expanded && (
        <>
          {/* Source file & line number */}
          {result.condition_line_number && (
            <DetailRow
              label="Source"
              value={`${result.source_file || ""} : Line ${result.condition_line_number}`}
              highlight
            />
          )}

          {/* Failure condition */}
          {result.failure_condition && (
            <div style={{ marginBottom: 8 }}>
              <span style={{ fontSize: "0.72rem", color: "var(--text-muted)", display: "block", marginBottom: 4 }}>
                Failing Condition
              </span>
              <pre style={{
                fontFamily: "var(--font-mono)", fontSize: "0.72rem", whiteSpace: "pre-wrap",
                background: "var(--bg-surface)", padding: 8, borderRadius: 4,
                border: `1px solid ${color}40`,
              }}>
                {result.failure_condition}
              </pre>
              {result.resolved_condition && (
                <div style={{ marginTop: 4 }}>
                  <span style={{ fontSize: "0.68rem", color: "var(--text-muted)", display: "block", marginBottom: 2 }}>
                    Resolved Table Names
                  </span>
                  <pre style={{
                    fontFamily: "var(--font-mono)", fontSize: "0.7rem", whiteSpace: "pre-wrap",
                    background: "rgba(16,185,129,0.06)", padding: 6, borderRadius: 4,
                    border: "1px solid rgba(16,185,129,0.2)", color: "var(--success)",
                  }}>
                    {result.resolved_condition}
                  </pre>
                </div>
              )}
            </div>
          )}

          {/* Likely cause */}
          {result.likely_cause && (
            <DetailRow label="Likely Cause" value={result.likely_cause} />
          )}

          {/* Verification result */}
          {result.verification && (
            <div style={{ marginTop: 8, padding: 8, background: result.verification.verified ? "rgba(16,185,129,0.1)" : "rgba(239,68,68,0.08)", borderRadius: 4 }}>
              <span style={{ fontSize: "0.72rem", fontWeight: 700, color: result.verification.verified ? "var(--success)" : "var(--danger)" }}>
                {result.verification.verified ? "✓ " : "✗ "}
                Verification
              </span>
              <div style={{ fontSize: "0.78rem", marginTop: 4 }}>{result.verification.message}</div>
              {result.verification.rows != null && (
                <div style={{ fontSize: "0.72rem", color: "var(--text-muted)", marginTop: 2 }}>
                  Rows: {result.verification.rows?.toLocaleString()}
                </div>
              )}
            </div>
          )}

          {/* WHERE condition lines (for where_combination) */}
          {result.where_condition_lines?.length > 0 && (
            <div style={{ marginTop: 8 }}>
              <span style={{ fontSize: "0.72rem", color: "var(--text-muted)", display: "block", marginBottom: 4 }}>
                WHERE Conditions ({result.where_condition_lines.length})
              </span>
              {result.where_condition_lines.map((wc, j) => (
                <div key={j} style={{ display: "flex", gap: 8, fontSize: "0.72rem", marginBottom: 2, fontFamily: "var(--font-mono)" }}>
                  {wc.line_number ? (
                    <span style={{ color: "var(--accent)", minWidth: 60 }}>Line {wc.line_number}</span>
                  ) : (
                    <span style={{ color: "var(--text-muted)", minWidth: 60 }}>—</span>
                  )}
                  <span style={{ flex: 1, whiteSpace: "pre-wrap" }}>{wc.condition?.slice(0, 120)}{wc.condition?.length > 120 ? "…" : ""}</span>
                </div>
              ))}
            </div>
          )}

          {/* Deeper investigation (when verification fails) */}
          {result.data_availability?.deeper_investigation && (
            <div style={{ marginTop: 8, padding: 8, borderLeft: "3px solid var(--warning)", background: "rgba(245,158,11,0.08)", borderRadius: 4 }}>
              <span style={{ fontSize: "0.72rem", fontWeight: 700, color: "var(--warning)" }}>Deeper Investigation</span>
              <div style={{ fontSize: "0.78rem", marginTop: 4 }}>
                {result.data_availability.deeper_investigation.explanation}
              </div>
              {result.data_availability.deeper_investigation.failure_condition && (
                <pre style={{ fontFamily: "var(--font-mono)", fontSize: "0.72rem", whiteSpace: "pre-wrap", marginTop: 4 }}>
                  {result.data_availability.deeper_investigation.failure_condition}
                </pre>
              )}
              {result.data_availability.deeper_investigation.condition_line_number && (
                <div style={{ fontSize: "0.72rem", color: "var(--accent)", marginTop: 4 }}>
                  Line {result.data_availability.deeper_investigation.condition_line_number}
                </div>
              )}
            </div>
          )}

          {/* Data availability summary */}
          {result.data_availability && (
            <DataAvailabilitySection data={result.data_availability} />
          )}

          {/* WHERE analysis (for where_condition) */}
          {result.where_analysis?.length > 0 && (
            <ConditionAnalysisSection title="WHERE Analysis" items={result.where_analysis} />
          )}

          {/* HAVING analysis */}
          {result.having_analysis?.length > 0 && (
            <ConditionAnalysisSection title="HAVING Analysis" items={result.having_analysis} />
          )}

          {/* Threshold suggestions */}
          {result.threshold_suggestions?.length > 0 && (
            <div style={{ marginTop: 8 }}>
              <span style={{ fontSize: "0.72rem", color: "var(--text-muted)", display: "block", marginBottom: 4 }}>
                Threshold Suggestions
              </span>
              {result.threshold_suggestions.map((s, j) => (
                <div key={j} style={{ fontSize: "0.72rem", marginBottom: 4, padding: 4, background: "var(--bg-surface)", borderRadius: 4 }}>
                  <strong style={{ fontFamily: "var(--font-mono)" }}>{s.column}</strong>
                  {s.suggestion && <span style={{ marginLeft: 8 }}>{s.suggestion}</span>}
                </div>
              ))}
            </div>
          )}
        </>
      )}
    </div>
  );
}

function DetailRow({ label, value, highlight }) {
  return (
    <div style={{ display: "flex", gap: 8, marginBottom: 6, fontSize: "0.8rem" }}>
      <span style={{ color: "var(--text-muted)", minWidth: 100, flexShrink: 0 }}>{label}</span>
      <span style={{
        flex: 1,
        color: highlight ? "var(--accent)" : "inherit",
        fontWeight: highlight ? 700 : 400,
      }}>
        {value}
      </span>
    </div>
  );
}

function DataAvailabilitySection({ data }) {
  const hasData = data && (
    data.explanation ||
    data.rows_without_outer_where != null ||
    data.rows_without_inner_having != null ||
    data.union_all_branch_counts?.length > 0
  );
  if (!hasData) return null;
  return (
    <div style={{ marginTop: 8, padding: 8, background: "var(--bg-surface)", borderRadius: 4 }}>
      <span style={{ fontSize: "0.72rem", color: "var(--text-muted)", display: "block", marginBottom: 4 }}>
        Data Availability
      </span>
      {data.explanation && (
        <div style={{ fontSize: "0.78rem", marginBottom: 6 }}>{data.explanation}</div>
      )}
      {data.rows_without_outer_where != null && (
        <div style={{ fontSize: "0.72rem", color: "var(--text-muted)" }}>
          Rows without outer WHERE: <strong>{data.rows_without_outer_where?.toLocaleString()}</strong>
        </div>
      )}
      {data.rows_without_inner_having != null && (
        <div style={{ fontSize: "0.72rem", color: "var(--text-muted)" }}>
          Rows without inner HAVING: <strong>{data.rows_without_inner_having?.toLocaleString()}</strong>
        </div>
      )}
      {data.union_all_branch_counts?.length > 0 && (
        <div style={{ marginTop: 6 }}>
          <span style={{ fontSize: "0.7rem", color: "var(--text-muted)" }}>UNION ALL Branches:</span>
          {data.union_all_branch_counts.map((b, j) => (
            <div key={j} style={{ fontSize: "0.72rem", fontFamily: "var(--font-mono)" }}>
              <span style={{ color: b.row_count > 0 ? "var(--success)" : "var(--danger)" }}>
                {b.row_count > 0 ? "✓" : "✗"}
              </span>
              {" "}Branch {b.branch_num}: {b.row_count?.toLocaleString()} rows — FROM {b.from_table}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function ConditionAnalysisSection({ title, items }) {
  return (
    <div style={{ marginTop: 8 }}>
      <span style={{ fontSize: "0.72rem", color: "var(--text-muted)", display: "block", marginBottom: 4 }}>
        {title}
      </span>
      {items.map((item, j) => (
        <div key={j} style={{ display: "flex", gap: 8, fontSize: "0.72rem", marginBottom: 3, fontFamily: "var(--font-mono)" }}>
          <span style={{ color: item.kills_rows ? "var(--danger)" : "var(--success)", minWidth: 60 }}>
            {item.kills_rows ? "✗ KILLS" : "✓ OK"}
          </span>
          <span style={{ minWidth: 50, color: "var(--text-muted)" }}>
            {item.rows_without_this_cond != null ? item.rows_without_this_cond.toLocaleString() : (item.rows_after != null ? item.rows_after.toLocaleString() : "—")}
          </span>
          <span style={{ flex: 1, whiteSpace: "pre-wrap" }}>{item.condition?.slice(0, 100)}{item.condition?.length > 100 ? "…" : ""}</span>
        </div>
      ))}
    </div>
  );
}
