import axios from "axios";

const BACKEND = (
  typeof window !== "undefined" && process.env.NEXT_PUBLIC_API_URL 
    ? process.env.NEXT_PUBLIC_API_URL 
    : "http://127.0.0.1:8000"
) + "/api";
const api = axios.create({ baseURL: BACKEND });

api.interceptors.request.use((config) => {
  const token = typeof window !== "undefined" ? localStorage.getItem("token") : null;
  if (token) config.headers.Authorization = `Bearer ${token}`;
  return config;
});

export const authAPI = {
  login: (username, password) =>
    api.post("/auth/login", new URLSearchParams({ username, password }), {
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
    }).then((r) => r.data),
  getMe: () => api.get("/auth/me").then((r) => r.data),
  logout: () => localStorage.removeItem("token"),
};

export const filesAPI = {
  uploadLog: (file, onProgress) => {
    const form = new FormData();
    form.append("file", file);
    return api.post("/upload", form, {
      onUploadProgress: (e) => onProgress?.(Math.round((e.loaded * 100) / e.total)),
    }).then((r) => r.data);
  },
};

export const jobsAPI = {
  runPipeline: (filePath, force = false) =>
    api.post(`/jobs/run?file_path=${encodeURIComponent(filePath)}&force=${force}`).then((r) => r.data),
  listJobs: () => api.get("/jobs").then((r) => r.data),
  getJob: (jobId) => api.get(`/jobs/${jobId}`).then((r) => r.data),
  rerunJob: (jobId, force = false) =>
    api.post(`/jobs/${jobId}/rerun?force=${force}`).then((r) => r.data),
  batchRun: (filePaths) =>
    api.post("/jobs/batch", { file_paths: filePaths }).then((r) => r.data),
  downloadReport: (jobId) =>
    api.get(`/jobs/${jobId}/report`, { responseType: "blob" }).then((r) => {
      const url = window.URL.createObjectURL(new Blob([r.data]));
      const link = document.createElement("a");
      link.href = url;
      link.setAttribute("download", `report_${jobId.slice(0, 8)}.pdf`);
      document.body.appendChild(link);
      link.click();
      link.remove();
      window.URL.revokeObjectURL(url);
    }),
  downloadBatchReport: (jobIds) =>
    api.post("/jobs/batch-report", null, {
      params: { job_ids: jobIds.join(",") },
      responseType: "blob",
    }).then((r) => {
      const ts = new Date().toISOString().replace(/[-:T]/g, "").slice(0, 14);
      const url = window.URL.createObjectURL(new Blob([r.data]));
      const link = document.createElement("a");
      link.href = url;
      link.setAttribute("download", `batch_report_${ts}.pdf`);
      document.body.appendChild(link);
      link.click();
      link.remove();
      window.URL.revokeObjectURL(url);
    }),
};

export const thresholdTuningAPI = {
  analyze: (filePath) =>
    api.post(`/threshold-tuning/analyze?file_path=${encodeURIComponent(filePath)}`).then((r) => r.data),
  recommend: (filePath, targetReductionPct) => {
    let url = `/threshold-tuning/recommend?file_path=${encodeURIComponent(filePath)}`;
    if (targetReductionPct !== null && targetReductionPct !== undefined && targetReductionPct !== "") {
      url += `&target_reduction_pct=${encodeURIComponent(targetReductionPct)}`;
    }
    return api.post(url).then((r) => r.data);
  },
  downloadReport: (analysis, recommendation) =>
    api.post(
      "/threshold-tuning/report",
      { analysis, recommendation },
      { responseType: "blob" }
    ).then((r) => {
      const disposition = r.headers?.["content-disposition"] || "";
      const match = disposition.match(/filename="?([^"]+)"?/);
      const filename = match ? match[1] : `${analysis.scenario_name}_threshold_tuning.pdf`;
      const url = window.URL.createObjectURL(new Blob([r.data]));
      const link = document.createElement("a");
      link.href = url;
      link.setAttribute("download", filename);
      document.body.appendChild(link);
      link.click();
      link.remove();
      window.URL.revokeObjectURL(url);
    }),
};

export const adminAPI = {
  listUsers: () => api.get("/admin/users").then((r) => r.data),
  createUser: (data) => api.post("/admin/users", data).then((r) => r.data),
  deactivateUser: (id) => api.delete(`/admin/users/${id}`).then((r) => r.data),
  getAuditLog: (limit = 100) => api.get(`/admin/audit?limit=${limit}`).then((r) => r.data),
};

export function getErrorMessage(err) {
  return err?.response?.data?.detail || err?.message || "An unexpected error occurred";
}
