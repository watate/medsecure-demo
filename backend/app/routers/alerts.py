import logging

import httpx
from fastapi import APIRouter, HTTPException, Query

from app.models.schemas import Alert, AlertsResponse
from app.services.database import get_db
from app.services.github_client import GitHubClient
from app.services.repo_resolver import (
    resolve_baseline_branch,
    resolve_branch,
    resolve_repo,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/alerts", tags=["alerts"])


@router.get("/live", response_model=AlertsResponse)
async def get_live_alerts(
    tool: str = Query(default="baseline", description="Tool name: baseline, devin, copilot, anthropic, openai, gemini"),
    state: str | None = Query(default=None, description="Filter by state: open, fixed, dismissed"),
    repo: str | None = Query(default=None, description="Repository (owner/repo)"),
    branch: str | None = Query(default=None, description="Explicit branch name (overrides tool-based lookup)"),
) -> AlertsResponse:
    """Fetch live alerts from GitHub API for a specific tool's branch."""
    resolved_repo = await resolve_repo(repo)
    resolved_branch = await resolve_branch(resolved_repo, tool, branch)
    github = GitHubClient(repo=resolved_repo)

    try:
        alerts = await github.get_alerts(resolved_branch, state=state)
        return AlertsResponse(branch=resolved_branch, tool=tool, total=len(alerts), alerts=alerts)
    except httpx.HTTPStatusError as e:
        logger.exception("Failed to fetch live alerts for branch %s", resolved_branch)
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
        logger.exception("Failed to fetch live alerts for branch %s", resolved_branch)
        raise HTTPException(status_code=502, detail=f"GitHub API error: {e}") from e


@router.get("/snapshot/{scan_id}", response_model=AlertsResponse)
async def get_snapshot_alerts(
    scan_id: int,
    tool: str = Query(default="baseline", description="Tool name"),
    branch: str | None = Query(default=None, description="Explicit branch name"),
    repo: str | None = Query(default=None, description="Repository (owner/repo)"),
) -> AlertsResponse:
    """Get stored alerts from a specific scan snapshot."""
    resolved_repo = await resolve_repo(repo)
    db = await get_db()
    try:
        cursor = await db.execute("SELECT repo FROM scans WHERE id = ?", (scan_id,))
        scan = await cursor.fetchone()
        if not scan or scan["repo"] != resolved_repo:
            raise HTTPException(status_code=404, detail="Scan not found")

        # Default to the baseline branch used for this scan.
        resolved_branch = branch
        if not resolved_branch:
            cursor = await db.execute(
                "SELECT branch FROM scan_branches WHERE scan_id = ? AND tool = 'baseline' LIMIT 1",
                (scan_id,),
            )
            row = await cursor.fetchone()
            resolved_branch = (
                row["branch"] if row else await resolve_baseline_branch(resolved_repo)
            )

        cursor = await db.execute(
            "SELECT * FROM alerts WHERE scan_id = ? AND branch = ?",
            (scan_id, resolved_branch),
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

        return AlertsResponse(branch=resolved_branch, tool=tool, total=len(alerts), alerts=alerts)
    finally:
        await db.close()
