from enum import Enum

from pydantic import BaseModel


class Severity(str, Enum):
    critical = "critical"
    high = "high"
    medium = "medium"
    low = "low"
    warning = "warning"
    note = "note"
    error = "error"


class AlertState(str, Enum):
    open = "open"
    closed = "closed"
    dismissed = "dismissed"
    fixed = "fixed"


class ToolName(str, Enum):
    baseline = "baseline"
    devin = "devin"
    copilot = "copilot"
    anthropic = "anthropic"
    openai = "openai"
    gemini = "gemini"


class Alert(BaseModel):
    number: int
    rule_id: str
    rule_description: str
    severity: str
    state: str
    tool: str
    file_path: str
    start_line: int
    end_line: int
    message: str
    html_url: str
    created_at: str
    dismissed_at: str | None = None
    fixed_at: str | None = None


class BranchSummary(BaseModel):
    branch: str
    tool: str
    total: int
    open: int
    fixed: int
    dismissed: int
    critical: int
    high: int
    medium: int
    low: int
    other: int
    estimated_prompt_tokens: int = 0


class ScanSnapshot(BaseModel):
    id: int
    repo: str
    created_at: str
    branches: dict[str, BranchSummary]


class ScanListItem(BaseModel):
    id: int
    repo: str
    created_at: str
    branch_count: int


class CostEstimate(BaseModel):
    model: str
    estimated_input_tokens: int
    estimated_output_tokens: int
    input_cost_usd: float
    output_cost_usd: float
    total_cost_usd: float
    pricing: dict[str, float]


class ComparisonResult(BaseModel):
    repo: str
    scanned_at: str
    baseline: BranchSummary
    tools: dict[str, BranchSummary]
    improvements: dict[str, dict[str, int | float]]
    cost_estimates: dict[str, CostEstimate] | None = None


class RemediationRequest(BaseModel):
    tool: ToolName = ToolName.devin
    alert_numbers: list[int] | None = None
    batch_size: int = 5


class DevinSession(BaseModel):
    id: int
    session_id: str
    alert_number: int
    rule_id: str
    file_path: str
    status: str
    pr_url: str | None = None
    created_at: str
    updated_at: str


class DevinSessionCreate(BaseModel):
    session_id: str
    alert_number: int
    rule_id: str
    file_path: str
    status: str = "running"


class RepoConfig(BaseModel):
    github_repo: str
    branch_baseline: str
    branch_devin: str
    branch_copilot: str
    branch_anthropic: str
    branch_openai: str
    branch_google: str


class HealthResponse(BaseModel):
    status: str
    version: str
    repo: str
    database: str


class TriggerScanResponse(BaseModel):
    scan_id: int
    repo: str
    branches_scanned: list[str]
    created_at: str


class RemediationResponse(BaseModel):
    sessions_created: int
    sessions: list[DevinSession]
    message: str


class AlertWithCWE(Alert):
    """Alert enriched with CWE IDs parsed from CodeQL rule tags."""

    cwe_ids: list[str] = []
    rule_tags: list[str] = []


class AlertsResponse(BaseModel):
    branch: str
    tool: str
    total: int
    alerts: list[Alert]


# --- Report schemas ---


class ReportRequest(BaseModel):
    scan_id: int | None = None
    avg_engineer_hourly_cost: float = 75.0
    avg_manual_fix_minutes: float = 30.0


class ReportMeta(BaseModel):
    report_type: str
    title: str
    generated_at: str
    repo: str
    scan_date: str


# --- Replay schemas ---


# --- API Remediation schemas ---


class ApiRemediationJob(BaseModel):
    id: int
    tool: str
    alert_number: int
    rule_id: str
    file_path: str
    status: str
    commit_sha: str | None = None
    error_message: str | None = None
    created_at: str
    updated_at: str


class ApiRemediationRequest(BaseModel):
    tool: str  # anthropic, openai, gemini
    alert_numbers: list[int]


class ApiRemediationResponse(BaseModel):
    tool: str
    total_alerts: int
    completed: int
    failed: int
    skipped: int
    jobs: list[ApiRemediationJob]
    message: str


# --- Replay schemas ---


class ReplayEvent(BaseModel):
    id: int
    run_id: int
    tool: str
    event_type: str
    detail: str
    alert_number: int | None = None
    timestamp_offset_ms: int
    metadata: dict[str, object] = {}
    created_at: str


class ReplayRun(BaseModel):
    id: int
    repo: str
    scan_id: int | None = None
    started_at: str
    ended_at: str | None = None
    status: str
    tools: list[str]
    branch_name: str | None = None


class ReplayRunWithEvents(ReplayRun):
    events: list[ReplayEvent]
    total_duration_ms: int | None = None
