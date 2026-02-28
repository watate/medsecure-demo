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
  estimated_prompt_tokens: number;
  unique_file_count: number;
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
  pricing_type: string; // "token", "per_request", "acu"
  estimated_input_tokens: number;
  estimated_output_tokens: number;
  input_cost_usd: number;
  output_cost_usd: number;
  total_cost_usd: number;
  pricing: Record<string, number>;
  // Per-request pricing (Copilot)
  alerts_processed: number;
  cost_per_request_usd: number;
  // ACU pricing (Devin)
  estimated_acus: number;
  cost_per_acu_usd: number;
  assumption: string | null;
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
  acus: number | null;
  created_at: string;
  updated_at: string;
}

export interface RepoConfig {
  github_repo: string;
  branch_baseline: string;
}

export interface HealthResponse {
  status: string;
  version: string;
  repo: string;
  database: string;
}

// Repo types
export interface Repo {
  id: number;
  full_name: string;
  default_branch: string;
  added_at: string;
}

export interface GitHubRepoInfo {
  full_name: string;
  description: string | null;
  default_branch: string;
  private: boolean;
  language: string | null;
  html_url: string;
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

// Benchmark types
export interface BenchmarkResponse {
  run_id: number;
  alert_count: number;
  severity_counts: Record<string, number>;
  tools: string[];
  message: string;
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
  cost_usd: number;
  cumulative_cost_usd: number;
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
  branch_name: string | null;
  total_cost_usd: number;
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

/** Build a query string from params, filtering out nullish values */
function qs(params: Record<string, string | number | boolean | null | undefined>): string {
  const entries = Object.entries(params).filter(
    ([, v]) => v !== null && v !== undefined && v !== ""
  );
  if (entries.length === 0) return "";
  return "?" + new URLSearchParams(entries.map(([k, v]) => [k, String(v)])).toString();
}

export const api = {
  // Health
  health: () => fetchApi<HealthResponse>("/api/health"),

  // Config
  getConfig: (repo?: string | null) =>
    fetchApi<RepoConfig>(`/api/config${qs({ repo })}`),

  // Repos
  listAvailableRepos: (search?: string) =>
    fetchApi<GitHubRepoInfo[]>(`/api/repos/available${qs({ search })}`),
  listTrackedRepos: () => fetchApi<Repo[]>("/api/repos"),
  addRepo: (fullName: string) =>
    fetchApi<Repo>("/api/repos", {
      method: "POST",
      body: JSON.stringify({ full_name: fullName }),
    }),
  removeRepo: (repoId: number) =>
    fetchApi<{ status: string }>(`/api/repos/${repoId}`, { method: "DELETE" }),

  // Scans
  triggerScan: (repo?: string | null) =>
    fetchApi<{ scan_id: number; repo: string; branches_scanned: string[]; created_at: string }>(
      `/api/scans/trigger${qs({ repo })}`,
      { method: "POST" }
    ),
  listScans: (repo?: string | null) =>
    fetchApi<ScanListItem[]>(`/api/scans${qs({ repo })}`),
  getLatestScan: (repo?: string | null) =>
    fetchApi<ScanSnapshot | null>(`/api/scans/latest${qs({ repo })}`),
  getScan: (id: number) => fetchApi<ScanSnapshot>(`/api/scans/${id}`),
  compareLatest: (repo?: string | null) =>
    fetchApi<ComparisonResult>(`/api/scans/compare/latest${qs({ repo })}`),

  // Alerts
  getLiveAlerts: (tool: string, state?: string, repo?: string | null) => {
    const params: Record<string, string | null | undefined> = { tool, repo };
    if (state) params.state = state;
    return fetchApi<AlertsResponse>(`/api/alerts/live${qs(params)}`);
  },
  getSnapshotAlerts: (scanId: number, tool: string, repo?: string | null) =>
    fetchApi<AlertsResponse>(`/api/alerts/snapshot/${scanId}${qs({ tool, repo })}`),

  // Remediation
  triggerDevinRemediation: (alertNumbers?: number[], batchSize?: number, repo?: string | null) =>
    fetchApi<{ sessions_created: number; sessions: DevinSession[]; message: string }>(
      `/api/remediate/devin${qs({ repo })}`,
      {
        method: "POST",
        body: JSON.stringify({
          tool: "devin",
          alert_numbers: alertNumbers || null,
          batch_size: batchSize || 5,
        }),
      }
    ),
  listDevinSessions: (repo?: string | null) =>
    fetchApi<DevinSession[]>(`/api/remediate/devin/sessions${qs({ repo })}`),
  refreshDevinSessions: (repo?: string | null) =>
    fetchApi<{ updated: number; total_running: number }>(
      `/api/remediate/devin/refresh${qs({ repo })}`,
      { method: "POST" }
    ),

  // API-based Remediation (Anthropic, OpenAI, Google)
  triggerApiRemediation: (tool: string, alertNumbers: number[], repo?: string | null) =>
    fetchApi<ApiRemediationResponse>(`/api/remediate/api-tool${qs({ repo })}`, {
      method: "POST",
      body: JSON.stringify({ tool, alert_numbers: alertNumbers }),
    }),
  listApiRemediationJobs: (tool?: string, repo?: string | null) => {
    return fetchApi<ApiRemediationJob[]>(`/api/remediate/api-tool/jobs${qs({ tool, repo })}`);
  },

  // Reports
  generateReport: (
    reportType: "ciso" | "cto",
    scanId?: number,
    avgCost?: number,
    avgMinutes?: number,
    repo?: string | null,
  ) =>
    fetchApi<ReportData>(`/api/reports/generate/${reportType}${qs({ repo })}`, {
      method: "POST",
      body: JSON.stringify({
        scan_id: scanId ?? null,
        avg_engineer_hourly_cost: avgCost ?? 75.0,
        avg_manual_fix_minutes: avgMinutes ?? 30.0,
      }),
    }),
  getLatestReport: (reportType: "ciso" | "cto", repo?: string | null) =>
    fetchApi<ReportData>(`/api/reports/latest/${reportType}${qs({ repo })}`),
  listReports: (reportType?: string, repo?: string | null) => {
    return fetchApi<ReportHistoryItem[]>(`/api/reports/history${qs({ report_type: reportType, repo })}`);
  },

  // Benchmark
  triggerBenchmark: (severities: string[], repo?: string | null) =>
    fetchApi<BenchmarkResponse>(`/api/remediate/benchmark${qs({ repo })}`, {
      method: "POST",
      body: JSON.stringify({ severities }),
    }),

  // Replay
  listReplayRuns: (repo?: string | null) =>
    fetchApi<ReplayRun[]>(`/api/replay/runs${qs({ repo })}`),
  getReplayRun: (runId: number) =>
    fetchApi<ReplayRunWithEvents>(`/api/replay/runs/${runId}`),
  seedDemoReplay: (repo?: string | null) =>
    fetchApi<{ run_id: number; events_created: number; message: string }>(
      `/api/replay/demo-seed${qs({ repo })}`,
      { method: "POST" }
    ),
};
