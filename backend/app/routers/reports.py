"""Report generation endpoints — CISO and CTO/VP Eng reports."""

import json
import logging

import httpx
from fastapi import APIRouter, HTTPException, Query

from app.config import settings
from app.models.schemas import BranchSummary, ReportRequest
from app.services.database import get_db
from app.services.github_client import GitHubClient
from app.services.report_generator import generate_ciso_report, generate_cto_report

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/reports", tags=["reports"])


def _get_branch_map() -> dict[str, str]:
    return {
        "baseline": settings.branch_baseline,
        "devin": settings.branch_devin,
        "copilot": settings.branch_copilot,
        "anthropic": settings.branch_anthropic,
    }


async def _fetch_alerts_for_report(
    github: GitHubClient, branch_map: dict[str, str],
) -> tuple[list[dict], dict[str, list[dict]]]:
    """Fetch CWE-enriched alerts for baseline and all tools.

    Returns (baseline_alerts_dicts, {tool_name: alerts_dicts}).
    """
    baseline_branch = branch_map["baseline"]
    baseline_alerts = await github.get_alerts_with_cwe(baseline_branch)
    baseline_dicts = [a.model_dump() for a in baseline_alerts]

    tool_alerts_map: dict[str, list[dict]] = {}
    for tool_name, branch in branch_map.items():
        if tool_name == "baseline":
            continue
        try:
            alerts = await github.get_alerts_with_cwe(branch)
            tool_alerts_map[tool_name] = [a.model_dump() for a in alerts]
        except httpx.HTTPStatusError as e:
            logger.warning("Failed to fetch alerts for %s (%s): %s", tool_name, branch, e)
            tool_alerts_map[tool_name] = []

    return baseline_dicts, tool_alerts_map


async def _get_summaries_from_scan(
    scan_id: int | None,
) -> tuple[BranchSummary | None, dict[str, BranchSummary] | None, str]:
    """Load scan summaries from DB. Returns (baseline_summary, tool_summaries, scan_date)."""
    db = await get_db()
    try:
        if scan_id:
            cursor = await db.execute("SELECT * FROM scans WHERE id = ?", (scan_id,))
        else:
            cursor = await db.execute("SELECT * FROM scans ORDER BY created_at DESC LIMIT 1")
        scan = await cursor.fetchone()
        if not scan:
            return None, None, ""

        actual_scan_id = scan["id"]
        scan_date = scan["created_at"]

        cursor = await db.execute("SELECT * FROM scan_branches WHERE scan_id = ?", (actual_scan_id,))
        rows = await cursor.fetchall()

        baseline_summary: BranchSummary | None = None
        tool_summaries: dict[str, BranchSummary] = {}
        for row in rows:
            summary = BranchSummary(
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
            if row["tool"] == "baseline":
                baseline_summary = summary
            else:
                tool_summaries[row["tool"]] = summary

        return baseline_summary, tool_summaries, scan_date
    finally:
        await db.close()


@router.post("/generate/{report_type}")
async def generate_report(
    report_type: str,
    request: ReportRequest,
) -> dict:
    """Generate a CISO or CTO report.

    report_type: 'ciso' or 'cto'
    """
    if report_type not in ("ciso", "cto"):
        raise HTTPException(status_code=400, detail="report_type must be 'ciso' or 'cto'")

    # Get scan summaries
    baseline_summary, tool_summaries, scan_date = await _get_summaries_from_scan(request.scan_id)
    if not baseline_summary:
        raise HTTPException(status_code=404, detail="No scan data found. Trigger a scan first.")

    # Fetch live CWE-enriched alerts
    github = GitHubClient()
    branch_map = _get_branch_map()

    try:
        baseline_dicts, tool_alerts_map = await _fetch_alerts_for_report(github, branch_map)
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 403:
            raise HTTPException(
                status_code=502,
                detail="GitHub API returned 403. Your GITHUB_TOKEN needs 'security_events' scope.",
            ) from e
        raise HTTPException(status_code=502, detail=f"GitHub API error: {e}") from e

    # Load remediation timing from replay events if available
    remediation_times = await _get_remediation_times(request.scan_id)

    if report_type == "ciso":
        assert tool_summaries is not None
        report = generate_ciso_report(
            repo=settings.github_repo,
            scan_created_at=scan_date,
            baseline_summary=baseline_summary,
            tool_summaries=tool_summaries,
            baseline_alerts=baseline_dicts,
            tool_alerts_map=tool_alerts_map,
            remediation_times=remediation_times,
        )
    else:
        assert tool_summaries is not None
        report = generate_cto_report(
            repo=settings.github_repo,
            scan_created_at=scan_date,
            baseline_summary=baseline_summary,
            tool_summaries=tool_summaries,
            baseline_alerts=baseline_dicts,
            tool_alerts_map=tool_alerts_map,
            remediation_times=remediation_times,
            avg_engineer_hourly_cost=request.avg_engineer_hourly_cost,
            avg_manual_fix_minutes=request.avg_manual_fix_minutes,
        )

    # Store report in DB
    db = await get_db()
    try:
        scan_id_val = request.scan_id or 0
        await db.execute(
            "INSERT INTO generated_reports (scan_id, report_type, report_data) VALUES (?, ?, ?)",
            (scan_id_val, report_type, json.dumps(report)),
        )
        await db.commit()
    finally:
        await db.close()

    return report


@router.get("/latest/{report_type}")
async def get_latest_report(report_type: str) -> dict:
    """Get the most recently generated report of a given type."""
    if report_type not in ("ciso", "cto"):
        raise HTTPException(status_code=400, detail="report_type must be 'ciso' or 'cto'")

    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT report_data FROM generated_reports WHERE report_type = ? ORDER BY created_at DESC LIMIT 1",
            (report_type,),
        )
        row = await cursor.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail=f"No {report_type} report found. Generate one first.")
        return json.loads(row["report_data"])
    finally:
        await db.close()


@router.get("/history")
async def list_reports(
    report_type: str | None = Query(default=None),
) -> list[dict]:
    """List all generated reports."""
    db = await get_db()
    try:
        if report_type:
            cursor = await db.execute(
                "SELECT id, scan_id, report_type, created_at"
                " FROM generated_reports WHERE report_type = ?"
                " ORDER BY created_at DESC",
                (report_type,),
            )
        else:
            cursor = await db.execute(
                "SELECT id, scan_id, report_type, created_at FROM generated_reports ORDER BY created_at DESC"
            )
        rows = await cursor.fetchall()
        return [
            {
                "id": row["id"],
                "scan_id": row["scan_id"],
                "report_type": row["report_type"],
                "created_at": row["created_at"],
            }
            for row in rows
        ]
    finally:
        await db.close()


async def _get_remediation_times(scan_id: int | None) -> dict[str, float] | None:
    """Get remediation timing from replay events if available."""
    db = await get_db()
    try:
        cursor = await db.execute(
            """SELECT re.tool, MAX(re.timestamp_offset_ms) as max_offset
               FROM replay_events re
               JOIN replay_runs rr ON re.run_id = rr.id
               WHERE (? IS NULL OR rr.scan_id = ?)
               GROUP BY re.tool""",
            (scan_id, scan_id),
        )
        rows = await cursor.fetchall()
        if not rows:
            return None
        return {row["tool"]: row["max_offset"] / 1000.0 for row in rows}
    finally:
        await db.close()
