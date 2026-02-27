import logging
from datetime import datetime, timezone

import httpx
from fastapi import APIRouter, HTTPException

from app.config import settings
from app.models.schemas import (
    BranchSummary,
    ComparisonResult,
    ScanListItem,
    ScanSnapshot,
    TriggerScanResponse,
)
from app.services.database import get_db
from app.services.github_client import GitHubClient

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/scans", tags=["scans"])


def _get_branch_map() -> dict[str, str]:
    """Return mapping of tool name to branch name."""
    return {
        "baseline": settings.branch_baseline,
        "devin": settings.branch_devin,
        "copilot": settings.branch_copilot,
        "anthropic": settings.branch_anthropic,
    }


@router.post("/trigger", response_model=TriggerScanResponse)
async def trigger_scan() -> TriggerScanResponse:
    """Trigger a new scan: fetch CodeQL alerts for all branches and store a snapshot."""
    github = GitHubClient()
    branch_map = _get_branch_map()
    now = datetime.now(timezone.utc).isoformat()

    db = await get_db()
    try:
        cursor = await db.execute(
            "INSERT INTO scans (repo, created_at) VALUES (?, ?)",
            (settings.github_repo, now),
        )
        scan_id = cursor.lastrowid
        assert scan_id is not None

        branches_scanned = []

        for tool_name, branch in branch_map.items():
            try:
                alerts = await github.get_alerts(branch)
                summary = github.compute_branch_summary(alerts, branch, tool_name)

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
                logger.info("Scanned branch %s (%s): %d alerts", branch, tool_name, summary.total)
            except httpx.HTTPStatusError as e:
                if e.response.status_code == 403:
                    logger.error(
                        "GitHub API 403 for branch %s (%s): token likely lacks "
                        "'security_events' scope or 'Code scanning alerts: Read' permission",
                        branch, tool_name,
                    )
                else:
                    logger.exception("Failed to scan branch %s (%s)", branch, tool_name)
            except Exception:
                logger.exception("Failed to scan branch %s (%s)", branch, tool_name)

        await db.commit()

        return TriggerScanResponse(
            scan_id=scan_id,
            repo=settings.github_repo,
            branches_scanned=branches_scanned,
            created_at=now,
        )
    finally:
        await db.close()


@router.get("", response_model=list[ScanListItem])
async def list_scans() -> list[ScanListItem]:
    """List all scan snapshots."""
    db = await get_db()
    try:
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
async def get_latest_scan() -> ScanSnapshot | None:
    """Get the most recent scan snapshot with branch summaries."""
    db = await get_db()
    try:
        cursor = await db.execute("SELECT id, repo, created_at FROM scans ORDER BY created_at DESC LIMIT 1")
        scan = await cursor.fetchone()
        if not scan:
            return None

        scan_id = scan["id"]
        cursor = await db.execute("SELECT * FROM scan_branches WHERE scan_id = ?", (scan_id,))
        branch_rows = await cursor.fetchall()

        branches = {}
        for row in branch_rows:
            branches[row["tool"]] = BranchSummary(
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
            )

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
            branches[row["tool"]] = BranchSummary(
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
            )

        return ScanSnapshot(id=scan["id"], repo=scan["repo"], created_at=scan["created_at"], branches=branches)
    finally:
        await db.close()


@router.get("/compare/latest", response_model=ComparisonResult)
async def compare_latest() -> ComparisonResult:
    """Get comparison of latest scan across all branches."""
    scan = await get_latest_scan()
    if not scan:
        raise HTTPException(status_code=404, detail="No scans found. Trigger a scan first.")

    baseline = scan.branches.get("baseline")
    if not baseline:
        raise HTTPException(status_code=404, detail="No baseline branch data found")

    tools = {k: v for k, v in scan.branches.items() if k != "baseline"}

    improvements: dict[str, dict[str, int | float]] = {}
    for tool_name, tool_summary in tools.items():
        improvements[tool_name] = {
            "total_fixed": baseline.open - tool_summary.open,
            "critical_fixed": baseline.critical - tool_summary.critical,
            "high_fixed": baseline.high - tool_summary.high,
            "medium_fixed": baseline.medium - tool_summary.medium,
            "low_fixed": baseline.low - tool_summary.low,
            "fix_rate_pct": round((1 - tool_summary.open / baseline.open) * 100, 1) if baseline.open > 0 else 0.0,
        }

    return ComparisonResult(
        repo=scan.repo,
        scanned_at=scan.created_at,
        baseline=baseline,
        tools=tools,
        improvements=improvements,
    )
