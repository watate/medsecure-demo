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
from app.services.replay_recorder import COPILOT_COST_PER_REQUEST, DEVIN_COST_PER_ACU
from app.services.repo_resolver import (
    get_latest_tool_branches,
    resolve_baseline_branch,
    resolve_repo,
)
from app.services.report_generator import _estimate_api_cost
from app.services.token_counter import (
    build_grouped_prompt_for_file,
    count_tokens,
    estimate_prompt_tokens_for_alert,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/scans", tags=["scans"])


def _row_to_branch_summary(row) -> BranchSummary:
    estimated_tokens = 0
    unique_files = 0
    try:
        # aiosqlite.Row supports `.keys()`
        keys = row.keys()
        if "estimated_prompt_tokens" in keys:
            estimated_tokens = row["estimated_prompt_tokens"]
        if "unique_file_count" in keys:
            unique_files = row["unique_file_count"]
    except Exception:
        estimated_tokens = 0
        unique_files = 0

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
        unique_file_count=unique_files,
    )


async def _compute_baseline_token_estimate(
    github: GitHubClient, alerts: list[Alert], branch: str,
) -> tuple[int, int]:
    """Estimate total prompt tokens for baseline open alerts using grouped prompts.

    Returns (estimated_tokens, unique_file_count).

    Alerts are grouped by file_path (matching actual remediation behaviour),
    so each unique file contributes one grouped prompt containing N alert
    contexts + 1 copy of the file content.

    Notes:
    - We dedupe by `file_path` so each source file is fetched once.
    - GitHub Contents API doesn't support true batching, but we do bounded
      concurrency to keep scans fast while avoiding bursts.
    - If we hit GitHub rate limits, we return (0, 0) so callers can fall back
      to the heuristic per-alert estimate.
    """

    open_alerts = [a for a in alerts if a.state.lower() == "open" and a.file_path]
    if not open_alerts:
        return 0, 0

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
        return 0, len(unique_paths)

    # Group alerts by file (matching remediation behaviour)
    from collections import defaultdict
    file_groups: dict[str, list[Alert]] = defaultdict(list)
    for alert in open_alerts:
        file_groups[alert.file_path].append(alert)

    total_tokens = 0
    for fpath, group_alerts in file_groups.items():
        file_content = file_cache.get(fpath, "")
        if len(group_alerts) == 1:
            # Single alert: use per-alert prompt
            a = group_alerts[0]
            total_tokens += estimate_prompt_tokens_for_alert(
                alert_rule_id=a.rule_id,
                alert_severity=a.severity,
                alert_rule_description=a.rule_description,
                alert_message=a.message,
                alert_file_path=a.file_path,
                alert_start_line=a.start_line,
                alert_end_line=a.end_line,
                file_content=file_content,
            )
        else:
            # Multiple alerts in same file: use grouped prompt (N contexts + 1 file)
            prompt = build_grouped_prompt_for_file(
                file_path=fpath,
                file_content=file_content,
                alerts=[
                    {
                        "rule_id": a.rule_id,
                        "severity": a.severity,
                        "rule_description": a.rule_description,
                        "message": a.message,
                        "start_line": a.start_line,
                        "end_line": a.end_line,
                    }
                    for a in group_alerts
                ],
            )
            total_tokens += count_tokens(prompt)

    return total_tokens, len(file_groups)


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
        estimated_tokens, unique_file_count = await _compute_baseline_token_estimate(
            github, baseline_alerts, baseline_branch
        )
        if estimated_tokens > 0 or unique_file_count > 0:
            await db.execute(
                "UPDATE scan_branches"
                " SET estimated_prompt_tokens = ?, unique_file_count = ?"
                " WHERE scan_id = ? AND tool = 'baseline'",
                (estimated_tokens, unique_file_count, scan_id),
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
    for tool_name, tool_summary in tools.items():
        improvements[tool_name] = {
            "total_fixed": baseline.open - tool_summary.open,
            "critical_fixed": baseline.critical - tool_summary.critical,
            "high_fixed": baseline.high - tool_summary.high,
            "medium_fixed": baseline.medium - tool_summary.medium,
            "low_fixed": baseline.low - tool_summary.low,
            "fix_rate_pct": round((1 - tool_summary.open / baseline.open) * 100, 1) if baseline.open > 0 else 0.0,
        }

    # Pre-remediation cost estimates for ALL 5 standard tools (based on
    # baseline data — does not require tool branches to exist).
    cost_estimates: dict[str, CostEstimate] = {}
    ALL_TOOLS = ["devin", "copilot", "anthropic", "openai", "gemini"]
    for tool_name in ALL_TOOLS:
        if tool_name in ("anthropic", "openai", "gemini"):
            # Token-based pricing for API tools
            cost_data = _estimate_api_cost(tool_name, baseline.open, baseline.estimated_prompt_tokens)
            if cost_data:
                cost_estimates[tool_name] = CostEstimate(**cost_data)
        elif tool_name == "copilot":
            # Copilot Autofix: $0.04 per request (flat rate per alert)
            total_cost = baseline.open * COPILOT_COST_PER_REQUEST
            cost_estimates[tool_name] = CostEstimate(
                model="Copilot Autofix",
                pricing_type="per_request",
                total_cost_usd=round(total_cost, 4),
                alerts_processed=baseline.open,
                cost_per_request_usd=COPILOT_COST_PER_REQUEST,
            )
        elif tool_name == "devin":
            # Devin: $2.00/ACU, estimated 0.09 ACU per session
            # 1 session per unique file (alerts grouped by file)
            session_count = baseline.unique_file_count if baseline.unique_file_count > 0 else baseline.open
            estimated_acus = session_count * 0.09
            total_cost = estimated_acus * DEVIN_COST_PER_ACU
            assumption = (
                f"Assumes ~0.09 ACU per session, {session_count} sessions "
                f"({baseline.open} alerts grouped into {session_count} files)"
            ) if baseline.unique_file_count > 0 else (
                "Assumes ~0.09 ACU per session (1 session per unique file)"
            )
            cost_estimates[tool_name] = CostEstimate(
                model="Devin (ACU-based)",
                pricing_type="acu",
                total_cost_usd=round(total_cost, 4),
                alerts_processed=baseline.open,
                estimated_acus=round(estimated_acus, 2),
                cost_per_acu_usd=DEVIN_COST_PER_ACU,
                assumption=assumption,
            )

    return ComparisonResult(
        repo=scan.repo,
        scanned_at=scan.created_at,
        baseline=baseline,
        tools=tools,
        improvements=improvements,
        cost_estimates=cost_estimates if cost_estimates else None,
    )
