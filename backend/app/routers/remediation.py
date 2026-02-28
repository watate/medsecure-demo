import logging

from fastapi import APIRouter, HTTPException

from app.config import settings
from app.models.schemas import (
    ApiRemediationJob,
    ApiRemediationRequest,
    ApiRemediationResponse,
    DevinSession,
    RemediationRequest,
    RemediationResponse,
)
from app.services.database import get_db
from app.services.devin_client import DevinClient
from app.services.github_client import GitHubClient
from app.services.llm_client import call_llm_with_delay
from app.services.token_counter import build_prompt_for_alert

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/remediate", tags=["remediation"])

# Map tool key → (config attr for branch, config attr for API key)
_API_TOOL_CONFIG = {
    "anthropic": ("branch_anthropic", "anthropic_api_key"),
    "openai": ("branch_openai", "openai_api_key"),
    "gemini": ("branch_google", "gemini_api_key"),
}


@router.post("/devin", response_model=RemediationResponse)
async def trigger_devin_remediation(request: RemediationRequest) -> RemediationResponse:
    """Create Devin sessions to fix CodeQL alerts on the devin branch."""
    if not settings.devin_api_key:
        raise HTTPException(status_code=400, detail="DEVIN_API_KEY not configured")

    github = GitHubClient()
    devin = DevinClient()
    branch = settings.branch_devin

    # Get open alerts
    alerts = await github.get_alerts(branch, state="open")

    if request.alert_numbers:
        alerts = [a for a in alerts if a.number in request.alert_numbers]

    if not alerts:
        return RemediationResponse(sessions_created=0, sessions=[], message="No open alerts to remediate")

    # Limit batch size
    alerts = alerts[: request.batch_size]

    db = await get_db()
    sessions_created: list[DevinSession] = []

    try:
        for alert in alerts:
            # Check if we already have a session for this alert
            cursor = await db.execute(
                "SELECT * FROM devin_sessions WHERE alert_number = ? AND status NOT IN ('failed', 'stopped')",
                (alert.number,),
            )
            existing = await cursor.fetchone()
            if existing:
                logger.info("Skipping alert %d, already has session %s", alert.number, existing["session_id"])
                continue

            try:
                result = await devin.create_remediation_session(alert, settings.github_repo, branch)
                session_id = result.get("session_id", "")

                await db.execute(
                    """INSERT INTO devin_sessions (session_id, alert_number, rule_id, file_path, status)
                       VALUES (?, ?, ?, ?, 'running')""",
                    (session_id, alert.number, alert.rule_id, alert.file_path),
                )

                cursor = await db.execute(
                    "SELECT * FROM devin_sessions WHERE session_id = ?",
                    (session_id,),
                )
                row = await cursor.fetchone()
                if row:
                    sessions_created.append(
                        DevinSession(
                            id=row["id"],
                            session_id=row["session_id"],
                            alert_number=row["alert_number"],
                            rule_id=row["rule_id"],
                            file_path=row["file_path"],
                            status=row["status"],
                            pr_url=row["pr_url"],
                            created_at=row["created_at"],
                            updated_at=row["updated_at"],
                        )
                    )

                logger.info(
                    "Created Devin session %s for alert %d (%s)",
                    session_id,
                    alert.number,
                    alert.rule_id,
                )
            except Exception:
                logger.exception("Failed to create Devin session for alert %d", alert.number)

        await db.commit()

        return RemediationResponse(
            sessions_created=len(sessions_created),
            sessions=sessions_created,
            message=f"Created {len(sessions_created)} Devin sessions for {len(alerts)} alerts",
        )
    finally:
        await db.close()


@router.get("/devin/sessions", response_model=list[DevinSession])
async def list_devin_sessions() -> list[DevinSession]:
    """List all Devin remediation sessions."""
    db = await get_db()
    try:
        cursor = await db.execute("SELECT * FROM devin_sessions ORDER BY created_at DESC")
        rows = await cursor.fetchall()

        return [
            DevinSession(
                id=row["id"],
                session_id=row["session_id"],
                alert_number=row["alert_number"],
                rule_id=row["rule_id"],
                file_path=row["file_path"],
                status=row["status"],
                pr_url=row["pr_url"],
                created_at=row["created_at"],
                updated_at=row["updated_at"],
            )
            for row in rows
        ]
    finally:
        await db.close()


@router.post("/api-tool", response_model=ApiRemediationResponse)
async def trigger_api_remediation(request: ApiRemediationRequest) -> ApiRemediationResponse:
    """Trigger remediation using an API-based tool (Anthropic, OpenAI, or Google).

    For each selected alert:
    1. Fetch the source file from the tool's branch
    2. Build a remediation prompt (alert context + full source file)
    3. Call the LLM API to generate a fix
    4. Commit the fixed file back to the tool's branch via GitHub Contents API
    """
    tool = request.tool
    if tool not in _API_TOOL_CONFIG:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown API tool: {tool}. Must be one of: {list(_API_TOOL_CONFIG.keys())}",
        )

    branch_attr, key_attr = _API_TOOL_CONFIG[tool]
    branch = getattr(settings, branch_attr)
    api_key = getattr(settings, key_attr)

    if not api_key:
        raise HTTPException(
            status_code=400,
            detail=f"{key_attr.upper()} not configured. Set it in your .env file.",
        )

    github = GitHubClient()

    # Fetch open alerts on the tool's branch
    alerts = await github.get_alerts(branch, state="open")

    # Filter to requested alert numbers
    requested_set = set(request.alert_numbers)
    alerts = [a for a in alerts if a.number in requested_set]

    if not alerts:
        return ApiRemediationResponse(
            tool=tool,
            total_alerts=0,
            completed=0,
            failed=0,
            skipped=0,
            jobs=[],
            message="No matching open alerts found on this branch",
        )

    db = await get_db()
    jobs: list[ApiRemediationJob] = []
    completed = 0
    failed = 0
    skipped = 0

    try:
        for alert in alerts:
            # Skip if we already have a successful job for this alert+tool
            cursor = await db.execute(
                "SELECT * FROM api_remediation_jobs WHERE tool = ? AND alert_number = ? AND status = 'completed'",
                (tool, alert.number),
            )
            existing = await cursor.fetchone()
            if existing:
                logger.info("Skipping alert %d for %s — already remediated", alert.number, tool)
                skipped += 1
                continue

            # Insert a pending job row
            cursor = await db.execute(
                """INSERT INTO api_remediation_jobs (tool, alert_number, rule_id, file_path, status)
                   VALUES (?, ?, ?, ?, 'running')""",
                (tool, alert.number, alert.rule_id, alert.file_path),
            )
            job_id = cursor.lastrowid
            await db.commit()

            try:
                # 1. Fetch source file
                file_content = await github.get_file_content(alert.file_path, branch)

                # 2. Build prompt
                prompt = build_prompt_for_alert(
                    alert_rule_id=alert.rule_id,
                    alert_severity=alert.severity,
                    alert_rule_description=alert.rule_description,
                    alert_message=alert.message,
                    alert_file_path=alert.file_path,
                    alert_start_line=alert.start_line,
                    alert_end_line=alert.end_line,
                    file_content=file_content,
                )

                # 3. Call LLM (with inter-call delay for rate limiting)
                logger.info("Calling %s for alert %d (%s)", tool, alert.number, alert.rule_id)
                fixed_content = await call_llm_with_delay(tool, prompt)

                if not fixed_content or not fixed_content.strip():
                    raise ValueError("LLM returned empty response")

                # 4. Commit the fix to the tool's branch
                commit_msg = f"fix: remediate CodeQL alert #{alert.number} ({alert.rule_id}) via {tool}"
                commit_sha = await github.update_file_content(
                    path=alert.file_path,
                    new_content=fixed_content,
                    branch=branch,
                    commit_message=commit_msg,
                )

                # 5. Update job status
                await db.execute(
                    """UPDATE api_remediation_jobs
                       SET status = 'completed', commit_sha = ?, updated_at = datetime('now')
                       WHERE id = ?""",
                    (commit_sha, job_id),
                )
                await db.commit()
                completed += 1

                logger.info(
                    "Successfully remediated alert %d via %s (commit %s)",
                    alert.number, tool, commit_sha[:8] if commit_sha else "unknown",
                )

            except Exception as e:
                error_msg = str(e)[:500]
                logger.exception("Failed to remediate alert %d via %s", alert.number, tool)
                await db.execute(
                    """UPDATE api_remediation_jobs
                       SET status = 'failed', error_message = ?, updated_at = datetime('now')
                       WHERE id = ?""",
                    (error_msg, job_id),
                )
                await db.commit()
                failed += 1

        # Fetch all jobs we created/touched for the response
        cursor = await db.execute(
            """SELECT * FROM api_remediation_jobs
               WHERE tool = ? AND alert_number IN ({})
               ORDER BY created_at DESC""".format(",".join("?" * len(request.alert_numbers))),
            (tool, *request.alert_numbers),
        )
        rows = await cursor.fetchall()
        jobs = [
            ApiRemediationJob(
                id=row["id"],
                tool=row["tool"],
                alert_number=row["alert_number"],
                rule_id=row["rule_id"],
                file_path=row["file_path"],
                status=row["status"],
                commit_sha=row["commit_sha"],
                error_message=row["error_message"],
                created_at=row["created_at"],
                updated_at=row["updated_at"],
            )
            for row in rows
        ]

        return ApiRemediationResponse(
            tool=tool,
            total_alerts=len(alerts),
            completed=completed,
            failed=failed,
            skipped=skipped,
            jobs=jobs,
            message=f"Remediation complete: {completed} fixed, {failed} failed, {skipped} skipped",
        )
    finally:
        await db.close()


@router.get("/api-tool/jobs", response_model=list[ApiRemediationJob])
async def list_api_remediation_jobs(tool: str | None = None) -> list[ApiRemediationJob]:
    """List API remediation jobs, optionally filtered by tool."""
    db = await get_db()
    try:
        if tool:
            cursor = await db.execute(
                "SELECT * FROM api_remediation_jobs WHERE tool = ? ORDER BY created_at DESC",
                (tool,),
            )
        else:
            cursor = await db.execute(
                "SELECT * FROM api_remediation_jobs ORDER BY created_at DESC"
            )
        rows = await cursor.fetchall()
        return [
            ApiRemediationJob(
                id=row["id"],
                tool=row["tool"],
                alert_number=row["alert_number"],
                rule_id=row["rule_id"],
                file_path=row["file_path"],
                status=row["status"],
                commit_sha=row["commit_sha"],
                error_message=row["error_message"],
                created_at=row["created_at"],
                updated_at=row["updated_at"],
            )
            for row in rows
        ]
    finally:
        await db.close()


@router.post("/devin/refresh")
async def refresh_devin_sessions() -> dict:
    """Refresh status of all running Devin sessions."""
    if not settings.devin_api_key:
        raise HTTPException(status_code=400, detail="DEVIN_API_KEY not configured")

    devin = DevinClient()
    db = await get_db()
    updated_count = 0

    try:
        cursor = await db.execute("SELECT * FROM devin_sessions WHERE status = 'running'")
        rows = await cursor.fetchall()

        for row in rows:
            try:
                status_data = await devin.get_session_status(row["session_id"])
                new_status = status_data.get("status_enum", "unknown")
                pr_url = status_data.get("pull_request", {}).get("url") if status_data.get("pull_request") else None

                await db.execute(
                    """UPDATE devin_sessions
                       SET status = ?, pr_url = ?, updated_at = datetime('now')
                       WHERE session_id = ?""",
                    (new_status, pr_url, row["session_id"]),
                )
                updated_count += 1
            except Exception:
                logger.exception("Failed to refresh session %s", row["session_id"])

        await db.commit()
        return {"updated": updated_count, "total_running": len(rows)}
    finally:
        await db.close()
