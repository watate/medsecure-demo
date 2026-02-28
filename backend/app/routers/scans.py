import asyncio
import base64
import logging
from datetime import datetime, timezone

import httpx
from fastapi import APIRouter, HTTPException, Query

from app.models.schemas import (
    Alert,
    BranchSummary,
    ComparisonResult,
    CostEstimate,
    ScanListItem,
    ScanSnapshot,
    TriggerScanResponse,
)
from app.services.database import get_db
from app.services.github_client import GitHubClient
from app.services.repo_resolver import (
    get_latest_tool_branches,
    resolve_baseline_branch,
    resolve_repo,
)
from app.services.report_generator import _estimate_api_cost
from app.services.token_counter import estimate_prompt_tokens_for_alert

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/scans", tags=["scans"])


def _row_to_branch_summary(row) -> BranchSummary:
    estimated_tokens = 0
    try:
        # aiosqlite.Row supports `.keys()`
        if "estimated_prompt_tokens" in row.keys():
            estimated_tokens = row["estimated_prompt_tokens"]
    except Exception:
        estimated_tokens = 0

    return BranchSummary(
        branch=row["branch"],
        tool=row["tool"],
        total=row["total"],
        open=row["open"],
        fixed=row["fixed"],
        dismissed=row["dismissed"],
        critical=row["critical"],
        high=row["high"],
        medium=row["medium"],
        low=row["low"],
        other=row["other"],
        estimated_prompt_tokens=estimated_tokens,
    )


async def _compute_baseline_token_estimate(github: GitHubClient, alerts: list[Alert], branch: str) -> int:
    """Estimate total prompt tokens for baseline open alerts.

    Notes:
    - We dedupe by `file_path` so each source file is fetched once.
    - GitHub Contents API doesn't support true batching, but we do bounded
      concurrency to keep scans fast while avoiding bursts.
    - If we hit GitHub rate limits, we return 0 so callers can fall back to the
      heuristic per-alert estimate.
    """

    open_alerts = [a for a in alerts if a.state.lower() == "open" and a.file_path]
    if not open_alerts:
        return 0

    unique_paths = sorted({a.file_path for a in open_alerts})
    file_cache: dict[str, str] = {}
    rate_limited = False

    sem = asyncio.Semaphore(5)

    async with httpx.AsyncClient(timeout=30.0) as client:

        async def _fetch(path: str) -> None:
            nonlocal rate_limited
            async with sem:
                try:
                    response = await client.get(
                        f"{github.BASE_URL}/repos/{github.repo}/contents/{path}",
                        headers=github.headers,
                        params={"ref": branch},
                    )
                    response.raise_for_status()
                    data = response.json()
                    file_cache[path] = base64.b64decode(data["content"]).decode("utf-8")
                except httpx.HTTPStatusError as e:
                    remaining = (e.response.headers or {}).get("X-RateLimit-Remaining")
                    if e.response.status_code == 403 and remaining == "0":
                        rate_limited = True
                    logger.warning("Failed to fetch file content for %s@%s: %s", path, branch, e)
                    file_cache[path] = ""
                except Exception as e:
                    logger.warning("Failed to fetch file content for %s@%s: %s", path, branch, e)
                    file_cache[path] = ""

        await asyncio.gather(*[_fetch(p) for p in unique_paths])

    if rate_limited:
        logger.warning("GitHub rate limited while fetching file contents; falling back to heuristic token estimate")
        return 0

    total_tokens = 0
    for alert in open_alerts:
        total_tokens += estimate_prompt_tokens_for_alert(
            alert_rule_id=alert.rule_id,
            alert_severity=alert.severity,
            alert_rule_description=alert.rule_description,
            alert_message=alert.message,
            alert_file_path=alert.file_path,
            alert_start_line=alert.start_line,
            alert_end_line=alert.end_line,
            file_content=file_cache.get(alert.file_path, ""),
        )

    return total_tokens


@router.post("/trigger", response_model=TriggerScanResponse)
async def trigger_scan(
    repo: str | None = Query(default=None, description="Repository (owner/repo). Defaults to first tracked repo."),
) -> TriggerScanResponse:
    """Trigger a new scan: fetch CodeQL alerts for the baseline branch and store a snapshot."""
    resolved_repo = await resolve_repo(repo)
    github = GitHubClient(repo=resolved_repo)
    baseline_branch = await resolve_baseline_branch(resolved_repo)
    tool_branches = await get_latest_tool_branches(resolved_repo)
    branch_map: dict[str, str] = {"baseline": baseline_branch, **tool_branches}
    now = datetime.now(timezone.utc).isoformat()

    db = await get_db()
    try:
        cursor = await db.execute(
            "INSERT INTO scans (repo, created_at) VALUES (?, ?)",
            (resolved_repo, now),
        )
        scan_id = cursor.lastrowid
        assert scan_id is not None

        branches_scanned: list[str] = []
        baseline_alerts: list[Alert] = []

        for tool_name, branch in branch_map.items():
            try:
                alerts = await github.get_alerts(branch)
                summary = github.compute_branch_summary(alerts, branch, tool_name)

                if tool_name == "baseline":
                    baseline_alerts = alerts

                await db.execute(
                    """INSERT INTO scan_branches
                       (scan_id, branch, tool, total, open, fixed, dismissed,
                        critical, high, medium, low, other)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        scan_id,
                        branch,
                        tool_name,
                        summary.total,
                        summary.open,
                        summary.fixed,
                        summary.dismissed,
                        summary.critical,
                        summary.high,
                        summary.medium,
                        summary.low,
                        summary.other,
                    ),
                )

                for alert in alerts:
                    await db.execute(
                        """INSERT INTO alerts
                           (scan_id, branch, alert_number, rule_id, rule_description,
                            severity, state, tool, file_path, start_line, end_line,
                            message, html_url, created_at, dismissed_at, fixed_at)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (
                            scan_id,
                            branch,
                            alert.number,
                            alert.rule_id,
                            alert.rule_description,
                            alert.severity,
                            alert.state,
                            alert.tool,
                            alert.file_path,
                            alert.start_line,
                            alert.end_line,
                            alert.message,
                            alert.html_url,
                            alert.created_at,
                            alert.dismissed_at,
                            alert.fixed_at,
                        ),
                    )

                branches_scanned.append(branch)
                logger.info("Scanned %s %s (%s): %d alerts", resolved_repo, tool_name, branch, summary.total)
            except httpx.HTTPStatusError as e:
                if e.response.status_code == 403:
                    logger.error(
                        "GitHub API 403 for %s (%s): token likely lacks "
                        "'security_events' scope or 'Code scanning alerts: Read' permission",
                        resolved_repo, branch,
                    )
                else:
                    logger.exception("Failed to scan %s (%s)", resolved_repo, branch)
            except Exception:
                logger.exception("Failed to scan %s (%s)", resolved_repo, branch)

        # Compute dynamic token estimates for baseline open alerts
        estimated_tokens = await _compute_baseline_token_estimate(
            github, baseline_alerts, baseline_branch
        )
        if estimated_tokens > 0:
            await db.execute(
                "UPDATE scan_branches SET estimated_prompt_tokens = ? WHERE scan_id = ? AND tool = 'baseline'",
                (estimated_tokens, scan_id),
            )

        await db.commit()

        return TriggerScanResponse(
            scan_id=scan_id,
            repo=resolved_repo,
            branches_scanned=branches_scanned,
            created_at=now,
        )
    finally:
        await db.close()


@router.get("", response_model=list[ScanListItem])
async def list_scans(
    repo: str | None = Query(default=None, description="Filter by repository"),
) -> list[ScanListItem]:
    """List all scan snapshots, optionally filtered by repo."""
    db = await get_db()
    try:
        if repo:
            cursor = await db.execute(
                """SELECT s.id, s.repo, s.created_at,
                          COUNT(sb.id) as branch_count
                   FROM scans s
                   LEFT JOIN scan_branches sb ON sb.scan_id = s.id
                   WHERE s.repo = ?
                   GROUP BY s.id
                   ORDER BY s.created_at DESC""",
                (repo,),
            )
        else:
            cursor = await db.execute(
                """SELECT s.id, s.repo, s.created_at,
                          COUNT(sb.id) as branch_count
                   FROM scans s
                   LEFT JOIN scan_branches sb ON sb.scan_id = s.id
                   GROUP BY s.id
                   ORDER BY s.created_at DESC"""
            )
        rows = await cursor.fetchall()
        return [
            ScanListItem(id=row["id"], repo=row["repo"], created_at=row["created_at"], branch_count=row["branch_count"])
            for row in rows
        ]
    finally:
        await db.close()


@router.get("/latest", response_model=ScanSnapshot | None)
async def get_latest_scan(
    repo: str | None = Query(default=None, description="Filter by repository"),
) -> ScanSnapshot | None:
    """Get the most recent scan snapshot with branch summaries."""
    db = await get_db()
    try:
        if repo:
            cursor = await db.execute(
                "SELECT id, repo, created_at FROM scans WHERE repo = ? ORDER BY created_at DESC LIMIT 1",
                (repo,),
            )
        else:
            cursor = await db.execute("SELECT id, repo, created_at FROM scans ORDER BY created_at DESC LIMIT 1")
        scan = await cursor.fetchone()
        if not scan:
            return None

        scan_id = scan["id"]
        cursor = await db.execute("SELECT * FROM scan_branches WHERE scan_id = ?", (scan_id,))
        branch_rows = await cursor.fetchall()

        branches = {}
        for row in branch_rows:
            branches[row["tool"]] = _row_to_branch_summary(row)

        return ScanSnapshot(id=scan_id, repo=scan["repo"], created_at=scan["created_at"], branches=branches)
    finally:
        await db.close()


@router.get("/{scan_id}", response_model=ScanSnapshot)
async def get_scan(scan_id: int) -> ScanSnapshot:
    """Get a specific scan snapshot."""
    db = await get_db()
    try:
        cursor = await db.execute("SELECT id, repo, created_at FROM scans WHERE id = ?", (scan_id,))
        scan = await cursor.fetchone()
        if not scan:
            raise HTTPException(status_code=404, detail="Scan not found")

        cursor = await db.execute("SELECT * FROM scan_branches WHERE scan_id = ?", (scan_id,))
        branch_rows = await cursor.fetchall()

        branches = {}
        for row in branch_rows:
            branches[row["tool"]] = _row_to_branch_summary(row)

        return ScanSnapshot(id=scan["id"], repo=scan["repo"], created_at=scan["created_at"], branches=branches)
    finally:
        await db.close()


@router.get("/compare/latest", response_model=ComparisonResult)
async def compare_latest(
    repo: str | None = Query(default=None, description="Filter by repository"),
) -> ComparisonResult:
    """Get comparison of latest scan across all branches."""
    scan = await get_latest_scan(repo=repo)
    if not scan:
        raise HTTPException(status_code=404, detail="No scans found. Trigger a scan first.")

    baseline = scan.branches.get("baseline")
    if not baseline:
        raise HTTPException(status_code=404, detail="No baseline branch data found")

    tools = {k: v for k, v in scan.branches.items() if k != "baseline"}

    improvements: dict[str, dict[str, int | float]] = {}
    cost_estimates: dict[str, CostEstimate] = {}
    for tool_name, tool_summary in tools.items():
        improvements[tool_name] = {
            "total_fixed": baseline.open - tool_summary.open,
            "critical_fixed": baseline.critical - tool_summary.critical,
            "high_fixed": baseline.high - tool_summary.high,
            "medium_fixed": baseline.medium - tool_summary.medium,
            "low_fixed": baseline.low - tool_summary.low,
            "fix_rate_pct": round((1 - tool_summary.open / baseline.open) * 100, 1) if baseline.open > 0 else 0.0,
        }

        # Pre-remediation cost estimate for API-based tools (using dynamic token count)
        cost_data = _estimate_api_cost(tool_name, baseline.open, baseline.estimated_prompt_tokens)
        if cost_data:
            cost_estimates[tool_name] = CostEstimate(**cost_data)

    return ComparisonResult(
        repo=scan.repo,
        scanned_at=scan.created_at,
        baseline=baseline,
        tools=tools,
        improvements=improvements,
        cost_estimates=cost_estimates if cost_estimates else None,
    )
