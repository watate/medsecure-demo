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


class ComparisonResult(BaseModel):
    repo: str
    scanned_at: str
    baseline: BranchSummary
    tools: dict[str, BranchSummary]
    improvements: dict[str, dict[str, int]]


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


class AlertsResponse(BaseModel):
    branch: str
    tool: str
    total: int
    alerts: list[Alert]
