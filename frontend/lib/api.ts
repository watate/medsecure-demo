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

export interface ComparisonResult {
  repo: string;
  scanned_at: string;
  baseline: BranchSummary;
  tools: Record<string, BranchSummary>;
  improvements: Record<string, Record<string, number>>;
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
}

export interface HealthResponse {
  status: string;
  version: string;
  repo: string;
  database: string;
}

async function fetchApi<T>(path: string, options?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      ...options?.headers,
    },
  });
  if (!res.ok) {
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
};
