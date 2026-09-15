"use client";

import { useState, useEffect } from "react";
import { useParams, useRouter, useSearchParams } from "next/navigation";
import { useAuth } from "@/hooks/use-auth";
import { useWebSocket } from "@/hooks/use-websocket";
import { Navbar } from "@/components/layout/navbar";
import { PipelineTracker } from "./pipeline-tracker";
import { CteWaterfall } from "./cte-waterfall";
import { RootCauseCard } from "./root-cause-card";
import { jobsAPI } from "@/lib/api-client";

const PIPELINE_STEPS = [
  { key: "log_reader", label: "Log Reader", desc: "Extract metadata & SQL" },
  { key: "set_batch_date", label: "Set Batch Date", desc: "SSH to OFSAA server" },
  { key: "sql_executer", label: "SQL Executer", desc: "Run full scenario SQL" },
  { key: "cte_parser", label: "CTE Parser", desc: "Split SQL into CTEs" },
  { key: "cte_executer", label: "CTE Executer", desc: "Execute each CTE" },
  { key: "sql_diagnostics", label: "SQL Diagnostics", desc: "Diagnose root cause" },
];

export default function JobPage() {
  const { jobId } = useParams();
  const router = useRouter();
  const searchParams = useSearchParams();
  const batchIds = searchParams.get("batch");
  const { user, logout } = useAuth();
  const [job, setJob] = useState(null);
  const [stepStates, setStepStates] = useState({});
  const [stepOutputs, setStepOutputs] = useState({});
  const [jobStatus, setJobStatus] = useState(null);
  const [alertsGenerated, setAlertsGenerated] = useState(null);
  const [rootCause, setRootCause] = useState(null);
  const [aiRecommendation, setAiRecommendation] = useState(null);
  const [diagnosticResults, setDiagnosticResults] = useState([]);
  const [cteResults, setCteResults] = useState([]);
  const [wsStatus, setWsStatus] = useState("connecting");
  const [exporting, setExporting] = useState(false);

  // Load initial job data
  useEffect(() => {
    jobsAPI.getJob(jobId).then((data) => {
      setJob(data);
      const states = {};
      const outputs = {};
      data.steps?.forEach((s) => { states[s.step_name] = s.status; outputs[s.step_name] = s.output || ""; });
      setStepStates(states);
      setStepOutputs(outputs);

      if (data.status === "completed" || data.status === "failed") {
        setJobStatus(data.status);
        setWsStatus("closed");
        if (data.status === "completed") {
          setAlertsGenerated(data.alerts_generated === 1);
          setRootCause(data.root_cause);
          setAiRecommendation(data.ai_recommendation);
          if (data.result_json) {
            try { setDiagnosticResults(JSON.parse(data.result_json)); } catch {}
          }
          if (data.cte_results_json) {
            try { setCteResults(JSON.parse(data.cte_results_json)); } catch {}
          }
        }
      }
    }).catch(() => router.replace("/dashboard"));
  }, [jobId]);

  // WebSocket for live updates
  const { connect } = useWebSocket(jobId, {
    onOpen: () => setWsStatus("live"),
    onMessage: (msg) => {
      switch (msg.event) {
        case "step_started":
          setStepStates((p) => ({ ...p, [msg.step]: "running" }));
          break;
        case "step_completed":
          setStepStates((p) => ({ ...p, [msg.step]: "completed" }));
          if (msg.output) setStepOutputs((p) => ({ ...p, [msg.step]: msg.output }));
          break;
        case "step_failed":
          setStepStates((p) => ({ ...p, [msg.step]: "failed" }));
          setJobStatus("failed");
          break;
        case "job_completed":
          setJobStatus("completed");
          setAlertsGenerated(!!msg.alerts_generated);
          setRootCause(msg.root_cause);
          setAiRecommendation(msg.ai_recommendation);
          if (msg.results) setDiagnosticResults(msg.results);
          if (msg.cte_results) setCteResults(msg.cte_results);
          break;
        case "job_failed":
          setJobStatus("failed");
          break;
      }
    },
    onError: () => setWsStatus("error"),
  });

  useEffect(() => {
    if (job && jobStatus !== "completed" && jobStatus !== "failed") connect();
  }, [job, jobStatus]);

  const handleExportPdf = async () => {
    setExporting(true);
    try {
      await jobsAPI.downloadReport(jobId);
    } catch (e) {
      alert("Failed to export PDF. Please try again.");
    } finally {
      setExporting(false);
    }
  };

  if (!job) return <div style={{ display: "flex", justifyContent: "center", padding: 80 }}><div className="spinner" /></div>;

  const isRunning = jobStatus !== "completed" && jobStatus !== "failed";
  const showResults = jobStatus === "completed" && !alertsGenerated;

  return (
    <div className="page">
      <Navbar user={user} onLogout={logout} />
      <main className="page-content">
        <div className="page-header">
          <div>
            <p className="eyebrow-label">Job diagnosis</p>
            <h1 className="page-title" style={{ fontSize: "1.3rem" }}>{job.scenario_name || "Unknown"}</h1>
            <p className="page-subtitle" style={{ display: "flex", gap: 10, alignItems: "center" }}>
              <span style={{ fontFamily: "var(--font-mono)", color: "var(--accent-ink)" }}>{job.job_id?.slice(0, 8)}…</span>
              {job.batch_date && <span>{job.batch_date}</span>}
            </p>
          </div>
          {jobStatus === "completed" && (
            <div className="page-header-actions">
              {batchIds && (
                <button className="btn btn-secondary btn-sm" onClick={() => router.push(`/dashboard/batch?ids=${batchIds}`)}>
                  ← Back to Batch
                </button>
              )}
              <button className="btn btn-secondary btn-sm" onClick={() => router.push("/dashboard")}>
                🔍 Check New Log
              </button>
              <button className="btn btn-primary btn-sm" onClick={handleExportPdf} disabled={exporting}>
                {exporting ? "Exporting…" : "📄 Export PDF"}
              </button>
            </div>
          )}
        </div>
        <PipelineTracker steps={PIPELINE_STEPS} states={stepStates} outputs={stepOutputs} wsStatus={wsStatus} />
        {isRunning && (
          <div style={{ display: "flex", alignItems: "center", gap: 12, padding: 16, background: "var(--accent-subtle)", borderRadius: "var(--radius-md)", marginTop: 24 }}>
            <div className="spinner spinner-sm" />
            <span style={{ fontSize: "0.875rem", color: "var(--text-secondary)" }}>Pipeline running — results will appear automatically</span>
          </div>
        )}
        {jobStatus === "failed" && (
          <div className="alert alert-error" style={{ marginTop: 24 }}>
            <span>✕</span>
            <div><strong>Pipeline failed.</strong> Check the step that turned red above.</div>
          </div>
        )}
        {showResults && <CteWaterfall cteResults={cteResults} />}
        {showResults && (
          <RootCauseCard
            rootCause={rootCause}
            results={diagnosticResults}
            aiRecommendation={aiRecommendation}
            logFilePath={job.log_file_path}
          />
        )}
        <div style={{ height: 64 }} />
      </main>
    </div>
  );
}
