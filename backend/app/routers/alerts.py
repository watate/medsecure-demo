import logging

import httpx
from fastapi import APIRouter, HTTPException, Query

from app.config import settings
from app.models.schemas import Alert, AlertsResponse
from app.services.database import get_db
from app.services.github_client import GitHubClient

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/alerts", tags=["alerts"])


def _resolve_branch(tool: str) -> str:
    """Resolve a tool name to its branch."""
    branch_map = {
        "baseline": settings.branch_baseline,
        "devin": settings.branch_devin,
        "copilot": settings.branch_copilot,
        "anthropic": settings.branch_anthropic,
        "openai": settings.branch_openai,
    }
    branch = branch_map.get(tool)
    if not branch:
        raise HTTPException(status_code=400, detail=f"Unknown tool: {tool}")
    return branch


@router.get("/live", response_model=AlertsResponse)
async def get_live_alerts(
    tool: str = Query(default="baseline", description="Tool name: baseline, devin, copilot, anthropic, openai"),
    state: str | None = Query(default=None, description="Filter by state: open, fixed, dismissed"),
) -> AlertsResponse:
    """Fetch live alerts from GitHub API for a specific tool's branch."""
    branch = _resolve_branch(tool)
    github = GitHubClient()

    try:
        alerts = await github.get_alerts(branch, state=state)
        return AlertsResponse(branch=branch, tool=tool, total=len(alerts), alerts=alerts)
    except httpx.HTTPStatusError as e:
        logger.exception("Failed to fetch live alerts for branch %s", branch)
        if e.response.status_code == 403:
            raise HTTPException(
                status_code=502,
                detail=(
                    "GitHub API returned 403 Forbidden. "
                    "Your GITHUB_TOKEN likely lacks the 'security_events' scope "
                    "(classic PAT) or 'Code scanning alerts: Read' permission "
                    "(fine-grained PAT). "
                    "See: https://docs.github.com/en/rest/code-scanning"
                ),
            ) from e
        raise HTTPException(status_code=502, detail=f"GitHub API error: {e}") from e
    except Exception as e:
        logger.exception("Failed to fetch live alerts for branch %s", branch)
        raise HTTPException(status_code=502, detail=f"GitHub API error: {e}") from e


@router.get("/snapshot/{scan_id}", response_model=AlertsResponse)
async def get_snapshot_alerts(
    scan_id: int,
    tool: str = Query(default="baseline", description="Tool name"),
) -> AlertsResponse:
    """Get stored alerts from a specific scan snapshot."""
    branch = _resolve_branch(tool)

    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT * FROM alerts WHERE scan_id = ? AND branch = ?",
            (scan_id, branch),
        )
        rows = await cursor.fetchall()

        if not rows:
            raise HTTPException(status_code=404, detail="No alerts found for this scan/branch")

        alerts = [
            Alert(
                number=row["alert_number"],
                rule_id=row["rule_id"],
                rule_description=row["rule_description"],
                severity=row["severity"],
                state=row["state"],
                tool=row["tool"],
                file_path=row["file_path"],
                start_line=row["start_line"],
                end_line=row["end_line"],
                message=row["message"],
                html_url=row["html_url"],
                created_at=row["created_at"],
                dismissed_at=row["dismissed_at"],
                fixed_at=row["fixed_at"],
            )
            for row in rows
        ]

        return AlertsResponse(branch=branch, tool=tool, total=len(alerts), alerts=alerts)
    finally:
        await db.close()
