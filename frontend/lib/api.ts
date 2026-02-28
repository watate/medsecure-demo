const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

export interface BranchSummary {
  branch: string;
  tool: string;
  total: number;
  open: number;
  fixed: number;
  dismissed: number;
  critical: number;
  high: number;
  medium: number;
  low: number;
  other: number;
}

export interface ScanSnapshot {
  id: number;
  repo: string;
  created_at: string;
  branches: Record<string, BranchSummary>;
}

export interface ScanListItem {
  id: number;
  repo: string;
  created_at: string;
  branch_count: number;
}

export interface CostEstimate {
  model: string;
  estimated_input_tokens: number;
  estimated_output_tokens: number;
  input_cost_usd: number;
  output_cost_usd: number;
  total_cost_usd: number;
  pricing: Record<string, number>;
}

export interface ComparisonResult {
  repo: string;
  scanned_at: string;
  baseline: BranchSummary;
  tools: Record<string, BranchSummary>;
  improvements: Record<string, Record<string, number>>;
  cost_estimates: Record<string, CostEstimate> | null;
}

export interface Alert {
  number: number;
  rule_id: string;
  rule_description: string;
  severity: string;
  state: string;
  tool: string;
  file_path: string;
  start_line: number;
  end_line: number;
  message: string;
  html_url: string;
  created_at: string;
  dismissed_at: string | null;
  fixed_at: string | null;
}

export interface AlertsResponse {
  branch: string;
  tool: string;
  total: number;
  alerts: Alert[];
}

export interface DevinSession {
  id: number;
  session_id: string;
  alert_number: number;
  rule_id: string;
  file_path: string;
  status: string;
  pr_url: string | null;
  created_at: string;
  updated_at: string;
}

export interface RepoConfig {
  github_repo: string;
  branch_baseline: string;
  branch_devin: string;
  branch_copilot: string;
  branch_anthropic: string;
  branch_openai: string;
  branch_google: string;
}

export interface HealthResponse {
  status: string;
  version: string;
  repo: string;
  database: string;
}

// API Remediation types
export interface ApiRemediationJob {
  id: number;
  tool: string;
  alert_number: number;
  rule_id: string;
  file_path: string;
  status: string;
  commit_sha: string | null;
  error_message: string | null;
  created_at: string;
  updated_at: string;
}

export interface ApiRemediationResponse {
  tool: string;
  total_alerts: number;
  completed: number;
  failed: number;
  skipped: number;
  jobs: ApiRemediationJob[];
  message: string;
}

// Report types
// eslint-disable-next-line @typescript-eslint/no-explicit-any
export type ReportData = Record<string, any>;

export interface ReportHistoryItem {
  id: number;
  scan_id: number;
  report_type: string;
  created_at: string;
}

// Replay types
export interface ReplayEvent {
  id: number;
  run_id: number;
  tool: string;
  event_type: string;
  detail: string;
  alert_number: number | null;
  timestamp_offset_ms: number;
  metadata: Record<string, unknown>;
  created_at: string;
}

export interface ReplayRun {
  id: number;
  repo: string;
  scan_id: number | null;
  started_at: string;
  ended_at: string | null;
  status: string;
  tools: string[];
}

export interface ReplayRunWithEvents extends ReplayRun {
  events: ReplayEvent[];
  total_duration_ms: number | null;
}

async function fetchApi<T>(path: string, options?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    ...options,
    credentials: "include",
    headers: {
      "Content-Type": "application/json",
      ...options?.headers,
    },
  });
  if (!res.ok) {
    // Redirect to login on 401 (invalid/expired session)
    if (res.status === 401 && typeof window !== "undefined") {
      window.location.href = "/login";
      throw new Error("Session expired");
    }
    const error = await res.text();
    throw new Error(`API error ${res.status}: ${error}`);
  }
  return res.json() as Promise<T>;
}

export const api = {
  // Health
  health: () => fetchApi<HealthResponse>("/api/health"),

  // Config
  getConfig: () => fetchApi<RepoConfig>("/api/config"),

  // Scans
  triggerScan: () =>
    fetchApi<{ scan_id: number; repo: string; branches_scanned: string[]; created_at: string }>("/api/scans/trigger", {
      method: "POST",
    }),
  listScans: () => fetchApi<ScanListItem[]>("/api/scans"),
  getLatestScan: () => fetchApi<ScanSnapshot | null>("/api/scans/latest"),
  getScan: (id: number) => fetchApi<ScanSnapshot>(`/api/scans/${id}`),
  compareLatest: () => fetchApi<ComparisonResult>("/api/scans/compare/latest"),

  // Alerts
  getLiveAlerts: (tool: string, state?: string) => {
    const params = new URLSearchParams({ tool });
    if (state) params.set("state", state);
    return fetchApi<AlertsResponse>(`/api/alerts/live?${params}`);
  },
  getSnapshotAlerts: (scanId: number, tool: string) =>
    fetchApi<AlertsResponse>(`/api/alerts/snapshot/${scanId}?tool=${tool}`),

  // Remediation
  triggerDevinRemediation: (alertNumbers?: number[], batchSize?: number) =>
    fetchApi<{ sessions_created: number; sessions: DevinSession[]; message: string }>("/api/remediate/devin", {
      method: "POST",
      body: JSON.stringify({
        tool: "devin",
        alert_numbers: alertNumbers || null,
        batch_size: batchSize || 5,
      }),
    }),
  listDevinSessions: () => fetchApi<DevinSession[]>("/api/remediate/devin/sessions"),
  refreshDevinSessions: () =>
    fetchApi<{ updated: number; total_running: number }>("/api/remediate/devin/refresh", { method: "POST" }),

  // API-based Remediation (Anthropic, OpenAI, Google)
  triggerApiRemediation: (tool: string, alertNumbers: number[]) =>
    fetchApi<ApiRemediationResponse>("/api/remediate/api-tool", {
      method: "POST",
      body: JSON.stringify({ tool, alert_numbers: alertNumbers }),
    }),
  listApiRemediationJobs: (tool?: string) => {
    const params = tool ? `?tool=${tool}` : "";
    return fetchApi<ApiRemediationJob[]>(`/api/remediate/api-tool/jobs${params}`);
  },

  // Reports
  generateReport: (reportType: "ciso" | "cto", scanId?: number, avgCost?: number, avgMinutes?: number) =>
    fetchApi<ReportData>(`/api/reports/generate/${reportType}`, {
      method: "POST",
      body: JSON.stringify({
        scan_id: scanId ?? null,
        avg_engineer_hourly_cost: avgCost ?? 75.0,
        avg_manual_fix_minutes: avgMinutes ?? 30.0,
      }),
    }),
  getLatestReport: (reportType: "ciso" | "cto") =>
    fetchApi<ReportData>(`/api/reports/latest/${reportType}`),
  listReports: (reportType?: string) => {
    const params = reportType ? `?report_type=${reportType}` : "";
    return fetchApi<ReportHistoryItem[]>(`/api/reports/history${params}`);
  },

  // Replay
  listReplayRuns: () => fetchApi<ReplayRun[]>("/api/replay/runs"),
  getReplayRun: (runId: number) => fetchApi<ReplayRunWithEvents>(`/api/replay/runs/${runId}`),
  seedDemoReplay: () =>
    fetchApi<{ run_id: number; events_created: number; message: string }>("/api/replay/demo-seed", {
      method: "POST",
    }),
};
