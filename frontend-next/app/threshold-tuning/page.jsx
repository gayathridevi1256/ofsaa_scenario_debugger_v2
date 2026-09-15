"use client";

import { useState, useRef, useEffect, Suspense } from "react";
import { useSearchParams } from "next/navigation";
import { useAuth } from "@/hooks/use-auth";
import { Navbar } from "@/components/layout/navbar";
import { filesAPI, thresholdTuningAPI, getErrorMessage } from "@/lib/api-client";

function ThresholdTuningPageInner() {
  const { user, logout } = useAuth();
  const searchParams = useSearchParams();
  const incomingFilePath = searchParams.get("file_path");
  const [dragging, setDragging] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState("");
  const [result, setResult] = useState(null);
  const [filePath, setFilePath] = useState(null);
  const [fromJob, setFromJob] = useState(false);
  const [recommending, setRecommending] = useState(false);
  const [recommendations, setRecommendations] = useState(null);
  const [recResult, setRecResult] = useState(null);
  const [recError, setRecError] = useState("");
  const [targetPct, setTargetPct] = useState("");
  const [downloadingReport, setDownloadingReport] = useState(false);
  const [reportError, setReportError] = useState("");
  const fileRef = useRef(null);

  const analyzePath = async (path) => {
    setUploading(true);
    setError("");
    setResult(null);
    setRecommendations(null);
    setRecResult(null);
    setRecError("");
    try {
      setFilePath(path);
      const analysis = await thresholdTuningAPI.analyze(path);
      setResult(analysis);
    } catch (err) {
      setError(getErrorMessage(err));
    } finally {
      setUploading(false);
    }
  };

  // Arrived here from a scenario debugging job's "Tune These Thresholds"
  // button — the log file is already uploaded server-side, so skip the
  // upload step and analyze it directly.
  useEffect(() => {
    if (incomingFilePath) {
      setFromJob(true);
      analyzePath(incomingFilePath);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [incomingFilePath]);

  const handleFile = async (file) => {
    if (!file) return;
    if (!file.name.toLowerCase().endsWith(".log") && !file.name.toLowerCase().endsWith(".txt")) {
      setError("Only .log and .txt files are supported");
      return;
    }
    setFromJob(false);
    setUploading(true);
    setError("");
    setResult(null);
    setRecommendations(null);
    setRecResult(null);
    setRecError("");
    try {
      const uploaded = await filesAPI.uploadLog(file);
      setFilePath(uploaded.file_path);
      const analysis = await thresholdTuningAPI.analyze(uploaded.file_path);
      setResult(analysis);
    } catch (err) {
      setError(getErrorMessage(err));
    } finally {
      setUploading(false);
    }
  };

  const handleTuneThresholds = async () => {
    if (!filePath) return;
    setRecommending(true);
    setRecError("");
    try {
      const data = await thresholdTuningAPI.recommend(filePath, targetPct === "" ? null : Number(targetPct));
      setRecommendations(data.recommendations);
      setRecResult(data);
    } catch (err) {
      setRecError(getErrorMessage(err));
    } finally {
      setRecommending(false);
    }
  };

  const handleDownloadReport = async () => {
    if (!result || !recResult) return;
    setDownloadingReport(true);
    setReportError("");
    try {
      await thresholdTuningAPI.downloadReport(result, recResult);
    } catch (err) {
      setReportError(getErrorMessage(err));
    } finally {
      setDownloadingReport(false);
    }
  };

  const handleDrop = (e) => {
    e.preventDefault();
    setDragging(false);
    if (e.dataTransfer.files.length) handleFile(e.dataTransfer.files[0]);
  };

  return (
    <div className="page">
      <Navbar user={user} onLogout={logout} />
      <main className="page-content">
        <div className="animate-fade-in page-header">
          <div>
            <p className="eyebrow-label">Threshold tuning</p>
            <h1 className="page-title">Calibrate configured thresholds</h1>
            <p className="page-subtitle">Upload a scenario log to see its currently configured threshold values.</p>
          </div>
        </div>

        {fromJob && (
          <div className="alert alert-info animate-fade-in" style={{ marginBottom: 28 }}>
            <span>🎯</span>
            <span>Loaded straight from a scenario debugging job&rsquo;s threshold-kill diagnosis — no need to re-upload the log file.</span>
          </div>
        )}

        <div className="card animate-fade-in" style={{ marginBottom: 28 }}>
          <h2 style={{ fontFamily: "var(--font-display)", fontSize: "1.05rem", fontWeight: 700, marginBottom: 14 }}>Upload Log File</h2>
          {error && <div className="alert alert-error" style={{ marginBottom: 14 }}>{error}</div>}
          <div
            onDragOver={(e) => { e.preventDefault(); setDragging(true); }}
            onDragLeave={() => setDragging(false)}
            onDrop={handleDrop}
            onClick={() => fileRef.current?.click()}
            className={`dropzone${dragging ? " dragging" : ""}`}
          >
            <input
              ref={fileRef}
              type="file"
              accept=".log,.txt"
              style={{ display: "none" }}
              onChange={(e) => handleFile(e.target.files?.[0])}
            />
            {uploading ? (
              <div>
                <div className="spinner" style={{ margin: "0 auto 14px" }} />
                <div style={{ fontSize: "0.875rem", color: "var(--text-secondary)", fontWeight: 500 }}>
                  Setting batch date and executing the dataset query — this can take 30-60s…
                </div>
              </div>
            ) : (
              <>
                <div className="dropzone-icon">
                  <svg width="22" height="22" viewBox="0 0 24 24" fill="none">
                    <path d="M4 19h16M7 15l3-8 3 6 2-4 2 6" stroke="#fff" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
                  </svg>
                </div>
                <div style={{ fontWeight: 700, color: "var(--text-primary)" }}>Drop a .log file here or click to browse</div>
                <div style={{ fontSize: "0.78rem", color: "var(--text-muted)", marginTop: 5 }}>Max 50MB</div>
              </>
            )}
          </div>
        </div>

        {result && <AlertCountCard result={result} />}
        {result && (
          <TuneButton
            recommending={recommending}
            recommendations={recommendations}
            recError={recError}
            targetPct={targetPct}
            onTargetPctChange={setTargetPct}
            onClick={handleTuneThresholds}
            isZeroAlerts={result.alert_count === 0}
          />
        )}
        {recResult && <ProjectedAlertCountCard recResult={recResult} />}
        {recResult && (
          <ReportCard
            downloading={downloadingReport}
            error={reportError}
            onClick={handleDownloadReport}
          />
        )}
        {result && <ThresholdTable result={result} recommendations={recommendations} />}

        <div style={{ height: 64 }} />
      </main>
    </div>
  );
}

export default function ThresholdTuningPage() {
  return (
    <Suspense fallback={<div style={{ display: "flex", justifyContent: "center", padding: 80 }}><div className="spinner" /></div>}>
      <ThresholdTuningPageInner />
    </Suspense>
  );
}

function AlertCountCard({ result }) {
  const zero = result.alert_count === 0;
  return (
    <div
      className="card animate-fade-in"
      style={{ marginBottom: 32, borderLeft: `4px solid ${zero ? "var(--danger)" : "var(--success)"}` }}
    >
      <h2 style={{ fontSize: "1rem", fontWeight: 700, marginBottom: 4, display: "flex", alignItems: "center", gap: 8 }}>
        <span style={{ color: zero ? "var(--danger)" : "var(--success)" }}>{zero ? "⚠" : "✓"}</span>
        Current Alert Count
      </h2>
      <p style={{ fontSize: "0.75rem", color: "var(--text-muted)", marginBottom: 12 }}>
        Full dataset query executed with today's batch date and the thresholds currently configured for threshold set {result.tshld_set_id}.
      </p>
      <div style={{ fontSize: "2rem", fontWeight: 700, color: zero ? "var(--danger)" : "var(--success)" }}>
        {result.alert_count.toLocaleString()} <span style={{ fontSize: "0.9rem", fontWeight: 500, color: "var(--text-muted)" }}>alert{result.alert_count === 1 ? "" : "s"}</span>
      </div>
    </div>
  );
}

const TARGET_PCT_PRESETS = [10, 20, 30, 50, 75];

function TuneButton({ recommending, recommendations, recError, targetPct, onTargetPctChange, onClick, isZeroAlerts }) {
  return (
    <div className="card animate-fade-in" style={{ marginBottom: 32 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 16, flexWrap: "wrap" }}>
        <button className="btn btn-primary" onClick={onClick} disabled={recommending}>
          {recommending ? "Tuning…" : recommendations ? "Re-tune the Thresholds" : "🎯 Tune the Thresholds"}
        </button>

        {!isZeroAlerts && (
          <>
            <label style={{ display: "flex", alignItems: "center", gap: 8, fontSize: "0.8rem", color: "var(--text-muted)" }}>
              Target reduction:
              <input
                type="number"
                min={1}
                max={100}
                placeholder="auto"
                value={targetPct}
                onChange={(e) => onTargetPctChange(e.target.value)}
                disabled={recommending}
                style={{
                  width: 64, padding: "4px 8px", borderRadius: "var(--radius-sm)",
                  border: "1px solid var(--border)", background: "var(--bg-elevated)", color: "var(--text)",
                }}
              />
              %
            </label>

            <div style={{ display: "flex", gap: 6 }}>
              {TARGET_PCT_PRESETS.map((p) => (
                <button
                  key={p}
                  type="button"
                  disabled={recommending}
                  onClick={() => onTargetPctChange(String(p))}
                  className="btn btn-secondary"
                  style={{
                    padding: "2px 10px", fontSize: "0.75rem",
                    ...(String(p) === String(targetPct) ? { borderColor: "var(--accent)", color: "var(--accent)" } : {}),
                  }}
                >
                  {p}%
                </button>
              ))}
              {targetPct !== "" && (
                <button
                  type="button"
                  disabled={recommending}
                  onClick={() => onTargetPctChange("")}
                  className="btn btn-secondary"
                  style={{ padding: "2px 10px", fontSize: "0.75rem" }}
                >
                  Clear
                </button>
              )}
            </div>
          </>
        )}
      </div>

      <p style={{ fontSize: "0.75rem", color: "var(--text-muted)", marginTop: 10, marginBottom: 0 }}>
        {isZeroAlerts
          ? "This scenario generates 0 alerts today — thresholds will be loosened toward the loosest value that still admits real data, within the configured range."
          : targetPct === ""
            ? "No target set — finds the largest safe change that still leaves at least one real alert."
            : `Finds the smallest change that gets real alert volume down by ~${targetPct}%, verified against the real dataset query.`}
      </p>

      {recommending && (
        <div style={{ fontSize: "0.8rem", color: "var(--text-muted)", marginTop: 10 }}>
          <span className="spinner" style={{ width: 14, height: 14, marginRight: 8, verticalAlign: "middle" }} />
          Sampling real data per threshold, then re-running the dataset query to find and verify the change — this can take several minutes…
        </div>
      )}
      {recError && <div style={{ fontSize: "0.8rem", color: "var(--danger)", marginTop: 10 }}>{recError}</div>}
      {recommendations && !recommending && (
        <div style={{ fontSize: "0.8rem", color: "var(--text-muted)", marginTop: 10 }}>
          💡 AI Recommendation column added below.
        </div>
      )}
    </div>
  );
}

function ProjectedAlertCountCard({ recResult }) {
  const {
    current_alert_count, projected_alert_count, alert_count_delta,
    target_reduction_pct, target_met, applied_changes, skipped_changes,
  } = recResult;

  const targetBadge = target_reduction_pct !== null && target_reduction_pct !== undefined && (
    <div
      style={{
        display: "inline-block", fontSize: "0.7rem", fontWeight: 600, padding: "2px 10px", borderRadius: 999,
        marginBottom: 10,
        background: target_met ? "var(--success-subtle, rgba(34,197,94,0.15))" : "var(--danger-subtle, rgba(239,68,68,0.15))",
        color: target_met ? "var(--success)" : "var(--danger)",
      }}
    >
      Target: {target_reduction_pct}% reduction — {target_met ? "reached" : "not reachable within configured range"}
    </div>
  );

  if (projected_alert_count === null || projected_alert_count === undefined) {
    return (
      <div className="card animate-fade-in" style={{ marginBottom: 32 }}>
        <h2 style={{ fontSize: "1rem", fontWeight: 700, marginBottom: 4 }}>Projected Alert Count</h2>
        {targetBadge}
        <p style={{ fontSize: "0.8rem", color: "var(--text-muted)" }}>
          No resolved recommendation actually differs from its current configured value, so there's nothing to re-run — the dataset query would produce the same {current_alert_count.toLocaleString()} alert{current_alert_count === 1 ? "" : "s"} it does today.
        </p>
      </div>
    );
  }

  const up = alert_count_delta > 0;
  const flat = alert_count_delta === 0;
  const isLoosen = current_alert_count === 0; // starting from 0 alerts — an increase is the goal, not a warning
  const color = flat ? "var(--text-muted)" : up ? (isLoosen ? "var(--success)" : "var(--warning, #d97706)") : "var(--success)";

  return (
    <div className="card animate-fade-in" style={{ marginBottom: 32, borderLeft: `4px solid ${color}` }}>
      <h2 style={{ fontSize: "1rem", fontWeight: 700, marginBottom: 4 }}>Projected Alert Count</h2>
      {targetBadge}
      <p style={{ fontSize: "0.75rem", color: "var(--text-muted)", marginBottom: 12 }}>
        The real dataset query, re-executed with {applied_changes.length} recommended threshold{applied_changes.length === 1 ? "" : "s"} substituted in place of {applied_changes.length === 1 ? "its" : "their"} current value{applied_changes.length === 1 ? "" : "s"}.
      </p>
      <div style={{ display: "flex", alignItems: "baseline", gap: 16, flexWrap: "wrap" }}>
        <div>
          <div style={{ fontSize: "0.7rem", color: "var(--text-muted)" }}>Current</div>
          <div style={{ fontSize: "1.5rem", fontWeight: 700 }}>{current_alert_count.toLocaleString()}</div>
        </div>
        <div style={{ fontSize: "1.25rem", color: "var(--text-muted)" }}>→</div>
        <div>
          <div style={{ fontSize: "0.7rem", color: "var(--text-muted)" }}>Projected</div>
          <div style={{ fontSize: "1.5rem", fontWeight: 700, color }}>{projected_alert_count.toLocaleString()}</div>
        </div>
        <div style={{ fontSize: "0.9rem", fontWeight: 600, color }}>
          ({flat ? "no change" : `${up ? "+" : ""}${alert_count_delta.toLocaleString()}`})
        </div>
      </div>

      {applied_changes.length > 0 && (
        <div style={{ marginTop: 14, fontSize: "0.75rem" }}>
          <div style={{ color: "var(--text-muted)", marginBottom: 4 }}>Applied to the re-run:</div>
          <ul style={{ margin: 0, paddingLeft: 18, color: "var(--text-muted)" }}>
            {applied_changes.map((c) => (
              <li key={c.name}>
                <span style={{ fontFamily: "var(--font-mono)", fontWeight: 600, color: "var(--text)" }}>{c.display_name}</span>
                : {fmtValue(c.current_value)} → {fmtValue(c.recommended_value)}
              </li>
            ))}
          </ul>
        </div>
      )}

      {skipped_changes.length > 0 && (
        <div style={{ marginTop: 10, fontSize: "0.7rem", color: "var(--text-muted)" }}>
          {skipped_changes.length} recommendation{skipped_changes.length === 1 ? "" : "s"} could not be safely applied to the re-run (not reflected in the projected count above) — {skipped_changes.map((s) => s.name).join(", ")}.
        </div>
      )}
    </div>
  );
}

function ReportCard({ downloading, error, onClick }) {
  return (
    <div className="card animate-fade-in" style={{ marginBottom: 32, display: "flex", alignItems: "center", gap: 16 }}>
      <button className="btn btn-secondary" onClick={onClick} disabled={downloading}>
        {downloading ? "Generating…" : "📄 Download PDF Report"}
      </button>
      {downloading && (
        <span style={{ fontSize: "0.8rem", color: "var(--text-muted)" }}>
          <span className="spinner" style={{ width: 14, height: 14, marginRight: 8, verticalAlign: "middle" }} />
          Formatting the results already computed above — no re-query needed, this should be quick.
        </span>
      )}
      {error && <span style={{ fontSize: "0.8rem", color: "var(--danger)" }}>{error}</span>}
      {!downloading && !error && (
        <span style={{ fontSize: "0.8rem", color: "var(--text-muted)" }}>
          Named with the scenario and threshold set — includes every threshold's current/recommended value and the verified alert-count impact.
        </span>
      )}
    </div>
  );
}

function fmtValue(v) {
  if (v === null || v === undefined) return "—";
  const n = Number(v);
  if (Number.isNaN(n)) return String(v);
  return Number.isInteger(n) ? n.toLocaleString() : n.toLocaleString(undefined, { maximumFractionDigits: 2 });
}

function ThresholdTable({ result, recommendations }) {
  return (
    <div className="card animate-fade-in">
      <h2 style={{ fontSize: "1rem", fontWeight: 700, marginBottom: 2 }}>{result.scenario_name}</h2>
      <p style={{ fontSize: "0.75rem", color: "var(--text-muted)", marginBottom: 16 }}>
        Threshold set ID {result.tshld_set_id} · {result.thresholds.length} configured thresholds
      </p>
      <div style={{ overflowX: "auto" }}>
        <table style={{ width: "100%", borderCollapse: "collapse", fontSize: "0.8rem" }}>
          <thead>
            <tr style={{ borderBottom: "1px solid var(--border)", textAlign: "left" }}>
              <th style={{ padding: "8px 10px" }}>Threshold</th>
              <th style={{ padding: "8px 10px" }}>Current Value</th>
              <th style={{ padding: "8px 10px" }}>Allowed Min</th>
              <th style={{ padding: "8px 10px" }}>Allowed Max</th>
              <th style={{ padding: "8px 10px" }}>Unit</th>
              {recommendations && <th style={{ padding: "8px 10px", minWidth: 220 }}>💡 AI Recommendation</th>}
              <th style={{ padding: "8px 10px", minWidth: 260 }}>Description</th>
            </tr>
          </thead>
          <tbody>
            {result.thresholds.map((t, i) => {
              const rec = recommendations?.[t.name];
              return (
                <tr
                  key={t.name}
                  style={{ borderBottom: "1px solid var(--border)", background: i % 2 === 1 ? "var(--bg-elevated)" : "transparent" }}
                >
                  <td style={{ padding: "8px 10px", fontFamily: "var(--font-mono)", fontWeight: 600, whiteSpace: "nowrap" }}>
                    {t.display_name}
                  </td>
                  <td style={{ padding: "8px 10px", fontFamily: "var(--font-mono)", color: "var(--accent)", fontWeight: 600, whiteSpace: "nowrap" }}>
                    {t.current_value ?? "—"}
                  </td>
                  <td style={{ padding: "8px 10px", color: "var(--text-muted)", whiteSpace: "nowrap" }}>{t.min_value ?? "—"}</td>
                  <td style={{ padding: "8px 10px", color: "var(--text-muted)", whiteSpace: "nowrap" }}>{t.max_value ?? "—"}</td>
                  <td style={{ padding: "8px 10px", color: "var(--text-muted)", whiteSpace: "nowrap" }}>{t.unit || "—"}</td>
                  {recommendations && (
                    <td style={{ padding: "8px 10px" }}>
                      {rec ? (
                        <>
                          {rec.recommended_value !== null && rec.recommended_value !== undefined ? (
                            <div style={{ fontFamily: "var(--font-mono)", fontWeight: 700, color: "var(--success)" }}>
                              {fmtValue(rec.recommended_value)}{t.unit ? ` ${t.unit}` : ""}
                            </div>
                          ) : (
                            <div style={{ color: "var(--text-muted)", fontStyle: "italic" }}>N/A</div>
                          )}
                          <div style={{ fontSize: "0.7rem", color: "var(--text-muted)", marginTop: 2 }}>{rec.note}</div>
                        </>
                      ) : (
                        <span style={{ color: "var(--text-muted)" }}>—</span>
                      )}
                    </td>
                  )}
                  <td style={{ padding: "8px 10px", color: "var(--text-muted)" }}>{t.description || "—"}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}
