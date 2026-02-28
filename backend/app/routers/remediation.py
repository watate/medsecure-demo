import asyncio
import json
import logging
import time as _time
from collections import defaultdict
from datetime import datetime, timezone

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query

from app.config import settings
from app.models.schemas import (
    Alert,
    ApiRemediationJob,
    ApiRemediationRequest,
    ApiRemediationResponse,
    BenchmarkRequest,
    BenchmarkResponse,
    CopilotAutofixJob,
    CopilotAutofixRequest,
    CopilotAutofixResponse,
    DevinSession,
    RemediationRequest,
    RemediationResponse,
)
from app.services.database import get_db
from app.services.devin_client import DevinClient
from app.services.github_client import GitHubClient
from app.services.llm_client import call_llm_with_delay
from app.services.replay_recorder import (
    COPILOT_COST_PER_REQUEST,
    ReplayRecorder,
    compute_devin_session_cost,
    compute_llm_call_cost,
)
from app.services.repo_resolver import resolve_baseline_branch, resolve_repo
from app.services.token_counter import (
    build_grouped_prompt_for_file,
    build_prompt_for_alert,
    count_tokens,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/remediate", tags=["remediation"])

# Map tool key → config attr for API key
_API_TOOL_CONFIG = {
    "anthropic": "anthropic_api_key",
    "openai": "openai_api_key",
    "gemini": "gemini_api_key",
}


def _group_alerts_by_file(alerts: list[Alert]) -> dict[str, list[Alert]]:
    """Group alerts by file_path so multiple alerts in the same file
    can be processed together, avoiding conflicts."""
    groups: dict[str, list[Alert]] = defaultdict(list)
    for alert in alerts:
        groups[alert.file_path].append(alert)
    return dict(groups)


@router.post("/devin", response_model=RemediationResponse)
async def trigger_devin_remediation(
    request: RemediationRequest,
    repo: str | None = Query(default=None, description="Repository (owner/repo)"),
) -> RemediationResponse:
    """Create Devin sessions to fix CodeQL alerts.

    Creates a fresh branch from main via GitHub API, groups alerts by file,
    and creates one Devin session per file with all grouped alerts as context.
    """
    if not settings.devin_api_key:
        raise HTTPException(status_code=400, detail="DEVIN_API_KEY not configured")

    resolved_repo = await resolve_repo(repo)
    baseline_branch = await resolve_baseline_branch(resolved_repo)

    github = GitHubClient(repo=resolved_repo)
    devin = DevinClient()

    # Get open alerts from baseline branch
    alerts = await github.get_alerts(baseline_branch, state="open")

    if request.alert_numbers:
        alerts = [a for a in alerts if a.number in request.alert_numbers]

    if not alerts:
        return RemediationResponse(sessions_created=0, sessions=[], message="No open alerts to remediate")

    # Batch by file groups (batch_size counts files)
    batch_size = max(1, request.batch_size)
    file_groups_all = _group_alerts_by_file(alerts)
    selected_paths = sorted(file_groups_all.keys())[:batch_size]
    file_groups = {p: file_groups_all[p] for p in selected_paths}
    alerts = [a for p in selected_paths for a in file_groups_all[p]]

    # Create a fresh branch from baseline for this remediation run
    branch_name = f"remediate/devin-{int(__import__('time').time())}"
    try:
        await github.create_branch(branch_name, from_branch=baseline_branch)
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to create branch {branch_name}: {e}",
        ) from e

    # Start replay recording with the new branch name
    recorder = ReplayRecorder(tools=["devin"], branch_name=branch_name, repo=resolved_repo)
    await recorder.start()

    await recorder.record(
        tool="devin",
        event_type="scan_started",
        detail=f"CodeQL scan detected {len(alerts)} open alerts across {len(file_groups)} files",
        metadata={
            "repo": resolved_repo,
            "branch": branch_name,
            "source_branch": baseline_branch,
            "alert_count": len(alerts),
            "file_count": len(file_groups),
            "grouped_files": list(file_groups.keys()),
        },
    )

    db = await get_db()
    sessions_created: list[DevinSession] = []

    try:
        for file_path, file_alerts in file_groups.items():
            alert_nums = [a.number for a in file_alerts]

            # Check if we already have sessions for any of these alerts
            skipped_alerts: list[Alert] = []
            new_alerts: list[Alert] = []
            for alert in file_alerts:
                cursor = await db.execute(
                    "SELECT * FROM devin_sessions "
                    "WHERE repo = ? AND alert_number = ? "
                    "AND status NOT IN ('failed', 'stopped')",
                    (resolved_repo, alert.number),
                )
                existing = await cursor.fetchone()
                if existing:
                    logger.info("Skipping alert %d, already has session %s", alert.number, existing["session_id"])
                    await recorder.record(
                        tool="devin",
                        event_type="alert_skipped",
                        detail=f"Alert #{alert.number} already has active session {existing['session_id']}",
                        alert_number=alert.number,
                        metadata={
                            "rule_id": alert.rule_id,
                            "file_path": alert.file_path,
                            "existing_session_id": existing["session_id"],
                        },
                    )
                    skipped_alerts.append(alert)
                else:
                    new_alerts.append(alert)

            if not new_alerts:
                continue

            try:
                await recorder.record(
                    tool="devin",
                    event_type="session_created",
                    detail=(
                        f"Creating Devin session for {len(new_alerts)} alert(s) in {file_path}"
                    ),
                    alert_number=new_alerts[0].number,
                    metadata={
                        "file_path": file_path,
                        "alert_count": len(new_alerts),
                        "alert_numbers": [a.number for a in new_alerts],
                        "rules": [a.rule_id for a in new_alerts],
                        "severities": [a.severity for a in new_alerts],
                        "branch": branch_name,
                    },
                )

                # Use grouped session if multiple alerts, single otherwise
                if len(new_alerts) == 1:
                    result = await devin.create_remediation_session(
                        new_alerts[0], resolved_repo, branch_name,
                    )
                else:
                    result = await devin.create_grouped_session(
                        new_alerts, resolved_repo, branch_name,
                    )
                session_id = result.get("session_id", "")

                # Record a devin_sessions row per alert (all share same session_id)
                for alert in new_alerts:
                    await db.execute(
                        """INSERT INTO devin_sessions (repo, session_id, alert_number, rule_id, file_path, status)
                           VALUES (?, ?, ?, ?, ?, 'running')""",
                        (resolved_repo, session_id, alert.number, alert.rule_id, alert.file_path),
                    )
                # Commit after INSERTs to release SQLite write lock so
                # ReplayRecorder (which uses its own connection) can write.
                await db.commit()

                cursor = await db.execute(
                    "SELECT * FROM devin_sessions WHERE repo = ? AND session_id = ? LIMIT 1",
                    (resolved_repo, session_id),
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

                await recorder.record(
                    tool="devin",
                    event_type="analyzing",
                    detail=(
                        f"Devin session {session_id} started for "
                        f"{len(new_alerts)} alert(s) in {file_path}"
                    ),
                    alert_number=new_alerts[0].number,
                    metadata={
                        "session_id": session_id,
                        "file_path": file_path,
                        "alert_numbers": [a.number for a in new_alerts],
                        "branch": branch_name,
                    },
                )

                logger.info(
                    "Created Devin session %s for %d alerts in %s",
                    session_id,
                    len(new_alerts),
                    file_path,
                )
            except Exception as e:
                logger.exception("Failed to create Devin session for file %s", file_path)
                await recorder.record(
                    tool="devin",
                    event_type="error",
                    detail=f"Failed to create session for {file_path} (alerts {alert_nums}): {str(e)[:200]}",
                    alert_number=new_alerts[0].number,
                    metadata={
                        "error": str(e)[:500],
                        "file_path": file_path,
                        "alert_numbers": alert_nums,
                    },
                )

        await db.commit()

        # Record completion
        await recorder.record(
            tool="devin",
            event_type="remediation_complete",
            detail=(
                f"Created {len(sessions_created)} Devin session(s) for "
                f"{len(alerts)} alerts across {len(file_groups)} files on {branch_name}"
            ),
            metadata={
                "sessions_created": len(sessions_created),
                "total_alerts": len(alerts),
                "file_groups": len(file_groups),
                "branch": branch_name,
            },
        )
        await recorder.finish()

        return RemediationResponse(
            sessions_created=len(sessions_created),
            sessions=sessions_created,
            message=(
                f"Created {len(sessions_created)} Devin session(s) for "
                f"{len(alerts)} alerts on branch {branch_name}"
            ),
        )
    except Exception:
        await recorder.finish("failed")
        raise
    finally:
        await db.close()


@router.get("/devin/sessions", response_model=list[DevinSession])
async def list_devin_sessions(
    repo: str | None = Query(default=None, description="Repository (owner/repo)"),
) -> list[DevinSession]:
    """List Devin remediation sessions for a repo."""
    resolved_repo = await resolve_repo(repo)
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT * FROM devin_sessions WHERE repo = ? ORDER BY created_at DESC",
            (resolved_repo,),
        )
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
                acus=row["acus"] if "acus" in row.keys() else None,
                created_at=row["created_at"],
                updated_at=row["updated_at"],
            )
            for row in rows
        ]
    finally:
        await db.close()


@router.post("/api-tool", response_model=ApiRemediationResponse)
async def trigger_api_remediation(
    request: ApiRemediationRequest,
    repo: str | None = Query(default=None, description="Repository (owner/repo)"),
) -> ApiRemediationResponse:
    """Trigger remediation using an API-based tool (Anthropic, OpenAI, or Google).

    Creates a fresh branch from main, groups alerts by file, and for each file:
    1. Fetch the source file from the new branch
    2. Build a single remediation prompt with all alerts for that file
    3. Call the LLM API to generate a fix addressing all alerts at once
    4. Commit the fixed file back via GitHub Contents API (one commit per file)
    """
    tool = request.tool
    if tool not in _API_TOOL_CONFIG:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown API tool: {tool}. Must be one of: {list(_API_TOOL_CONFIG.keys())}",
        )

    key_attr = _API_TOOL_CONFIG[tool]
    api_key = getattr(settings, key_attr)

    if not api_key:
        raise HTTPException(
            status_code=400,
            detail=f"{key_attr.upper()} not configured. Set it in your .env file.",
        )

    resolved_repo = await resolve_repo(repo)
    baseline_branch = await resolve_baseline_branch(resolved_repo)

    github = GitHubClient(repo=resolved_repo)

    # Fetch open alerts from baseline branch
    alerts = await github.get_alerts(baseline_branch, state="open")

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
            message="No matching open alerts found",
        )

    # Create a fresh branch from main for this remediation run
    branch_name = f"remediate/{tool}-{int(__import__('time').time())}"
    try:
        await github.create_branch(branch_name, from_branch=baseline_branch)
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to create branch {branch_name}: {e}",
        ) from e

    # Group alerts by file to avoid conflicts
    file_groups = _group_alerts_by_file(alerts)

    # Start replay recording with the new branch
    recorder = ReplayRecorder(tools=[tool], branch_name=branch_name, repo=resolved_repo)
    await recorder.start()
    await recorder.record(
        tool=tool,
        event_type="scan_started",
        detail=(
            f"Starting {tool} remediation for {len(alerts)} alerts "
            f"across {len(file_groups)} files on {branch_name}"
        ),
        metadata={
            "repo": resolved_repo,
            "branch": branch_name,
            "source_branch": baseline_branch,
            "alert_count": len(alerts),
            "file_count": len(file_groups),
            "grouped_files": list(file_groups.keys()),
            "tool": tool,
        },
    )

    db = await get_db()
    jobs: list[ApiRemediationJob] = []
    completed = 0
    failed = 0
    skipped = 0

    try:
        for file_path, file_alerts in file_groups.items():
            alert_nums = [a.number for a in file_alerts]

            # Skip alerts that already have a successful job
            new_alerts: list[Alert] = []
            for alert in file_alerts:
                cursor = await db.execute(
                    "SELECT * FROM api_remediation_jobs "
                    "WHERE repo = ? AND tool = ? AND alert_number = ? "
                    "AND status = 'completed'",
                    (resolved_repo, tool, alert.number),
                )
                existing = await cursor.fetchone()
                if existing:
                    logger.info("Skipping alert %d for %s — already remediated", alert.number, tool)
                    await recorder.record(
                        tool=tool,
                        event_type="alert_skipped",
                        detail=f"Alert #{alert.number} already remediated by {tool}",
                        alert_number=alert.number,
                        metadata={
                            "rule_id": alert.rule_id,
                            "file_path": alert.file_path,
                            "reason": "already_completed",
                        },
                    )
                    skipped += 1
                else:
                    new_alerts.append(alert)

            if not new_alerts:
                continue

            # Insert pending job rows for all alerts in this file group
            job_ids: list[int] = []
            for alert in new_alerts:
                cursor = await db.execute(
                    """INSERT INTO api_remediation_jobs (repo, tool, alert_number, rule_id, file_path, status)
                       VALUES (?, ?, ?, ?, ?, 'running')""",
                    (resolved_repo, tool, alert.number, alert.rule_id, alert.file_path),
                )
                job_ids.append(cursor.lastrowid or 0)
            await db.commit()

            try:
                # 1. Fetch source file
                await recorder.record(
                    tool=tool,
                    event_type="alert_triaged",
                    detail=(
                        f"Fetching {file_path} for {len(new_alerts)} alert(s): "
                        f"{', '.join(f'#{a.number}' for a in new_alerts)}"
                    ),
                    alert_number=new_alerts[0].number,
                    metadata={
                        "file_path": file_path,
                        "alert_count": len(new_alerts),
                        "alert_numbers": [a.number for a in new_alerts],
                        "rules": [a.rule_id for a in new_alerts],
                        "severities": [a.severity for a in new_alerts],
                    },
                )
                file_content = await github.get_file_content(file_path, branch_name)

                # 2. Build prompt — grouped if multiple alerts, single otherwise
                if len(new_alerts) == 1:
                    alert = new_alerts[0]
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
                else:
                    prompt = build_grouped_prompt_for_file(
                        file_path=file_path,
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
                            for a in new_alerts
                        ],
                    )

                prompt_tokens = count_tokens(prompt)

                # 3. Call LLM (with inter-call delay for rate limiting)
                logger.info(
                    "Calling %s for %d alert(s) in %s",
                    tool, len(new_alerts), file_path,
                )

                await recorder.record(
                    tool=tool,
                    event_type="api_call_sent",
                    detail=(
                        f"Sending {len(new_alerts)} grouped alert(s) for "
                        f"{file_path} to {tool}"
                    ),
                    alert_number=new_alerts[0].number,
                    metadata={
                        "prompt_tokens": prompt_tokens,
                        "prompt_preview": prompt[:500],
                        "source_file_length": len(file_content),
                        "file_path": file_path,
                        "alert_count": len(new_alerts),
                        "alert_numbers": [a.number for a in new_alerts],
                    },
                )

                llm_result = await call_llm_with_delay(tool, prompt)

                if not llm_result.extracted_code or not llm_result.extracted_code.strip():
                    raise ValueError("LLM returned empty response")

                # Compute cost for this LLM call
                call_cost = compute_llm_call_cost(
                    tool, llm_result.input_tokens, llm_result.output_tokens,
                )

                await recorder.record(
                    tool=tool,
                    event_type="patch_generated",
                    detail=(
                        f"{llm_result.model} generated fix for "
                        f"{len(new_alerts)} alert(s) in {file_path}"
                    ),
                    alert_number=new_alerts[0].number,
                    metadata={
                        "model": llm_result.model,
                        "latency_ms": llm_result.latency_ms,
                        "input_tokens": llm_result.input_tokens,
                        "output_tokens": llm_result.output_tokens,
                        "fixed_content_length": len(llm_result.extracted_code),
                        "source_file_length": len(file_content),
                        "file_path": file_path,
                        "alert_count": len(new_alerts),
                    },
                    cost_usd=call_cost,
                )

                # 4. Commit the fix — one commit per file
                alert_refs = ", ".join(f"#{a.number}" for a in new_alerts)
                commit_msg = (
                    f"fix: remediate {len(new_alerts)} CodeQL alert(s) "
                    f"({alert_refs}) in {file_path} via {tool}"
                )
                commit_sha = await github.update_file_content(
                    path=file_path,
                    new_content=llm_result.extracted_code,
                    branch=branch_name,
                    commit_message=commit_msg,
                )

                await recorder.record(
                    tool=tool,
                    event_type="patch_applied",
                    detail=f"Patch committed to {branch_name} for {file_path}",
                    alert_number=new_alerts[0].number,
                    metadata={
                        "commit_sha": commit_sha,
                        "branch": branch_name,
                        "file_path": file_path,
                        "commit_message": commit_msg,
                        "alert_numbers": [a.number for a in new_alerts],
                    },
                )

                # 5. Update all job statuses for this file group
                for jid in job_ids:
                    await db.execute(
                        """UPDATE api_remediation_jobs
                           SET status = 'completed', commit_sha = ?, updated_at = datetime('now')
                           WHERE id = ?""",
                        (commit_sha, jid),
                    )
                await db.commit()
                completed += len(new_alerts)

                logger.info(
                    "Successfully remediated %d alert(s) in %s via %s (commit %s)",
                    len(new_alerts), file_path, tool,
                    commit_sha[:8] if commit_sha else "unknown",
                )

            except Exception as e:
                error_msg = str(e)[:500]
                logger.exception("Failed to remediate file %s via %s", file_path, tool)
                for jid in job_ids:
                    await db.execute(
                        """UPDATE api_remediation_jobs
                           SET status = 'failed', error_message = ?, updated_at = datetime('now')
                           WHERE id = ?""",
                        (error_msg, jid),
                    )
                await db.commit()
                failed += len(new_alerts)

                await recorder.record(
                    tool=tool,
                    event_type="error",
                    detail=f"Failed to remediate {file_path}: {error_msg[:200]}",
                    alert_number=new_alerts[0].number,
                    metadata={
                        "error": error_msg,
                        "file_path": file_path,
                        "alert_numbers": alert_nums,
                    },
                )

        # Record completion summary
        await recorder.record(
            tool=tool,
            event_type="remediation_complete",
            detail=(
                f"Remediation complete on {branch_name}: {completed} fixed, "
                f"{failed} failed, {skipped} skipped out of {len(alerts)} alerts "
                f"across {len(file_groups)} files"
            ),
            metadata={
                "total_alerts": len(alerts),
                "completed": completed,
                "failed": failed,
                "skipped": skipped,
                "tool": tool,
                "branch": branch_name,
                "file_count": len(file_groups),
            },
        )
        await recorder.finish()

        # Fetch all jobs we created/touched for the response
        cursor = await db.execute(
            """SELECT * FROM api_remediation_jobs
               WHERE repo = ? AND tool = ? AND alert_number IN ({})
               ORDER BY created_at DESC""".format(",".join("?" * len(request.alert_numbers))),
            (resolved_repo, tool, *request.alert_numbers),
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
            message=(
                f"Remediation complete on {branch_name}: "
                f"{completed} fixed, {failed} failed, {skipped} skipped"
            ),
        )
    except Exception:
        await recorder.finish("failed")
        raise
    finally:
        await db.close()


@router.get("/api-tool/jobs", response_model=list[ApiRemediationJob])
async def list_api_remediation_jobs(
    tool: str | None = None,
    repo: str | None = Query(default=None, description="Repository (owner/repo)"),
) -> list[ApiRemediationJob]:
    """List API remediation jobs for a repo, optionally filtered by tool."""
    resolved_repo = await resolve_repo(repo)
    db = await get_db()
    try:
        if tool:
            cursor = await db.execute(
                "SELECT * FROM api_remediation_jobs WHERE repo = ? AND tool = ? ORDER BY created_at DESC",
                (resolved_repo, tool),
            )
        else:
            cursor = await db.execute(
                "SELECT * FROM api_remediation_jobs WHERE repo = ? ORDER BY created_at DESC",
                (resolved_repo,),
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
async def refresh_devin_sessions(
    repo: str | None = Query(default=None, description="Repository (owner/repo)"),
) -> dict:
    """Refresh status of running Devin sessions for a repo."""
    if not settings.devin_api_key:
        raise HTTPException(status_code=400, detail="DEVIN_API_KEY not configured")

    devin = DevinClient()
    db = await get_db()
    updated_count = 0

    try:
        resolved_repo = await resolve_repo(repo)
        cursor = await db.execute(
            "SELECT * FROM devin_sessions WHERE repo = ? AND status = 'running'",
            (resolved_repo,),
        )
        rows = await cursor.fetchall()

        for row in rows:
            try:
                status_data = await devin.get_session_status(row["session_id"])
                new_status = status_data.get("status_enum", "unknown")
                pr_url = status_data.get("pull_request", {}).get("url") if status_data.get("pull_request") else None

                # Extract ACU usage if available in the response
                acus = status_data.get("total_acus") or status_data.get("acus")

                await db.execute(
                    """UPDATE devin_sessions
                       SET status = ?, pr_url = ?, acus = COALESCE(?, acus), updated_at = datetime('now')
                       WHERE repo = ? AND session_id = ?""",
                    (new_status, pr_url, acus, row["repo"], row["session_id"]),
                )
                updated_count += 1
            except Exception:
                logger.exception("Failed to refresh session %s", row["session_id"])

        await db.commit()
        return {"updated": updated_count, "total_running": len(rows)}
    finally:
        await db.close()


# ---------------------------------------------------------------------------
# Copilot Autofix remediation
# ---------------------------------------------------------------------------

COPILOT_INTER_ALERT_DELAY = 2.0  # seconds between trigger calls


@router.post("/copilot", response_model=CopilotAutofixResponse)
async def trigger_copilot_remediation(
    request: CopilotAutofixRequest,
    repo: str | None = Query(default=None, description="Repository (owner/repo)"),
) -> CopilotAutofixResponse:
    """Trigger remediation using GitHub Copilot Autofix.

    Alerts are processed in batches of ``request.batch_size`` (default 10).
    Within each batch alerts are handled sequentially with a short delay
    between triggers to respect GitHub rate limits.  Between batches a
    longer pause is applied so we don't overwhelm the API.

    For each alert:
    1. Trigger Copilot Autofix generation via the REST API
    2. Poll until the autofix succeeds, fails, or times out
    3. If succeeded, commit the fix to a fresh branch
    4. Record every step as a replay event
    """
    import asyncio
    import time as _time

    resolved_repo = await resolve_repo(repo)
    baseline_branch = await resolve_baseline_branch(resolved_repo)

    # Use request override if provided, otherwise fall back to env var
    batch_size = max(1, request.batch_size or settings.batch_size)
    github = GitHubClient(repo=resolved_repo)

    # Fetch open alerts from baseline branch
    alerts = await github.get_alerts(baseline_branch, state="open")
    requested_set = set(request.alert_numbers)
    alerts = [a for a in alerts if a.number in requested_set]

    if not alerts:
        return CopilotAutofixResponse(
            total_alerts=0,
            completed=0,
            failed=0,
            skipped=0,
            jobs=[],
            message="No matching open alerts found",
        )

    # Group alerts by file — batch_size counts file groups, not alerts.
    # e.g. 3 alerts in one file = 1 slot.
    file_groups = _group_alerts_by_file(alerts)
    file_group_items = list(file_groups.items())

    # Split file groups into batches
    file_batches: list[list[tuple[str, list[Alert]]]] = [
        file_group_items[i : i + batch_size]
        for i in range(0, len(file_group_items), batch_size)
    ]

    # Create a fresh branch from main
    branch_name = f"remediate/copilot-{int(_time.time())}"
    try:
        await github.create_branch(
            branch_name, from_branch=baseline_branch,
        )
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to create branch {branch_name}: {e}",
        ) from e

    # Start replay recording
    recorder = ReplayRecorder(tools=["copilot"], branch_name=branch_name, repo=resolved_repo)
    await recorder.start()
    await recorder.record(
        tool="copilot",
        event_type="scan_started",
        detail=(
            f"Starting Copilot Autofix for {len(alerts)} alert(s) across "
            f"{len(file_group_items)} file(s) in {len(file_batches)} batch(es) "
            f"of up to {batch_size} files on {branch_name}"
        ),
        metadata={
            "repo": resolved_repo,
            "branch": branch_name,
            "source_branch": baseline_branch,
            "alert_count": len(alerts),
            "file_count": len(file_group_items),
            "batch_size": batch_size,
            "batch_count": len(file_batches),
            "alert_numbers": [a.number for a in alerts],
            "grouped_files": list(file_groups.keys()),
        },
    )

    db = await get_db()
    jobs: list[CopilotAutofixJob] = []
    completed = 0
    failed = 0
    skipped = 0
    recorder_finished = False

    try:
        for batch_num, file_batch in enumerate(file_batches):
            # Pause between batches (not before the first)
            if batch_num > 0:
                batch_delay = COPILOT_INTER_ALERT_DELAY * 2
                logger.info(
                    "Batch %d/%d complete, pausing %.1fs before next batch",
                    batch_num, len(file_batches), batch_delay,
                )
                await recorder.record(
                    tool="copilot",
                    event_type="batch_pause",
                    detail=(
                        f"Batch {batch_num}/{len(file_batches)} done — "
                        f"pausing {batch_delay:.0f}s before batch {batch_num + 1}"
                    ),
                    metadata={
                        "batch_num": batch_num,
                        "total_batches": len(file_batches),
                        "delay_seconds": batch_delay,
                    },
                )
                await asyncio.sleep(batch_delay)

            for file_path, file_alerts in file_batch:
                for alert_idx, alert in enumerate(file_alerts):
                    # Rate-limit: wait between triggers (skip for the very first)
                    if batch_num > 0 or file_path != file_batch[0][0] or alert_idx > 0:
                        await asyncio.sleep(COPILOT_INTER_ALERT_DELAY)

                    # Check if already processed
                    cursor = await db.execute(
                        "SELECT * FROM copilot_autofix_jobs "
                        "WHERE repo = ? AND alert_number = ? "
                        "AND status = 'completed'",
                        (resolved_repo, alert.number),
                    )
                    existing = await cursor.fetchone()
                    if existing:
                        logger.info(
                            "Skipping alert #%d — already remediated by Copilot",
                            alert.number,
                        )
                        await recorder.record(
                            tool="copilot",
                            event_type="alert_skipped",
                            detail=f"Alert #{alert.number} already remediated by Copilot",
                            alert_number=alert.number,
                            metadata={
                                "rule_id": alert.rule_id,
                                "file_path": alert.file_path,
                                "reason": "already_completed",
                            },
                        )
                        skipped += 1
                        continue

                    # Insert a pending job row
                    cursor = await db.execute(
                        """INSERT INTO copilot_autofix_jobs
                           (repo, alert_number, rule_id, file_path, status)
                           VALUES (?, ?, ?, ?, 'running')""",
                        (resolved_repo, alert.number, alert.rule_id, alert.file_path),
                    )
                    job_id = cursor.lastrowid or 0
                    await db.commit()

                    try:
                        # 1. Trigger autofix + poll
                        await recorder.record(
                            tool="copilot",
                            event_type="autofix_triggered",
                            detail=(
                                f"Triggering Copilot Autofix for alert #{alert.number} "
                                f"({alert.rule_id}) in {file_path}"
                            ),
                            alert_number=alert.number,
                            metadata={
                                "rule_id": alert.rule_id,
                                "file_path": alert.file_path,
                                "severity": alert.severity,
                            },
                            cost_usd=COPILOT_COST_PER_REQUEST,
                        )

                        autofix = await github.poll_autofix(alert.number)
                        autofix_status = autofix.get("status", "unknown")
                        description = autofix.get("description", "")

                        await recorder.record(
                            tool="copilot",
                            event_type="autofix_result",
                            detail=f"Autofix for alert #{alert.number}: {autofix_status}",
                            alert_number=alert.number,
                            metadata={
                                "autofix_status": autofix_status,
                                "description": description,
                                "rule_id": alert.rule_id,
                                "file_path": alert.file_path,
                            },
                        )

                        if autofix_status != "succeeded":
                            # Autofix didn't succeed — mark as failed
                            await db.execute(
                                """UPDATE copilot_autofix_jobs
                                   SET status = 'failed',
                                       autofix_status = ?,
                                       error_message = ?,
                                       updated_at = datetime('now')
                                   WHERE id = ?""",
                                (autofix_status, f"Autofix status: {autofix_status}", job_id),
                            )
                            await db.commit()
                            failed += 1
                            continue

                        # 2. Commit the fix to our branch
                        commit_msg = (
                            f"fix: Copilot Autofix for alert #{alert.number} "
                            f"({alert.rule_id}) in {alert.file_path}"
                        )
                        commit_result = await github.commit_autofix(
                            alert.number, branch_name, commit_msg,
                        )
                        commit_sha = commit_result.get("sha", "")

                        await recorder.record(
                            tool="copilot",
                            event_type="patch_applied",
                            detail=f"Copilot fix committed for alert #{alert.number}",
                            alert_number=alert.number,
                            metadata={
                                "commit_sha": commit_sha,
                                "branch": branch_name,
                                "file_path": alert.file_path,
                                "description": description,
                            },
                        )

                        # 3. Update job status
                        await db.execute(
                            """UPDATE copilot_autofix_jobs
                               SET status = 'completed',
                                   autofix_status = ?,
                                   commit_sha = ?,
                                   description = ?,
                                   updated_at = datetime('now')
                               WHERE id = ?""",
                            (autofix_status, commit_sha, description, job_id),
                        )
                        await db.commit()
                        completed += 1

                        logger.info(
                            "Copilot Autofix committed for alert #%d (commit %s)",
                            alert.number,
                            commit_sha[:8] if commit_sha else "unknown",
                        )

                    except Exception as e:
                        error_msg = str(e)[:500]
                        logger.exception(
                            "Failed Copilot Autofix for alert #%d", alert.number,
                        )
                        await db.execute(
                            """UPDATE copilot_autofix_jobs
                               SET status = 'failed',
                                   error_message = ?,
                                   updated_at = datetime('now')
                               WHERE id = ?""",
                            (error_msg, job_id),
                        )
                        await db.commit()
                        failed += 1

                        await recorder.record(
                            tool="copilot",
                            event_type="error",
                            detail=f"Failed autofix for alert #{alert.number}: {error_msg[:200]}",
                            alert_number=alert.number,
                            metadata={
                                "error": error_msg,
                                "rule_id": alert.rule_id,
                                "file_path": alert.file_path,
                            },
                        )

        # Record completion summary
        await recorder.record(
            tool="copilot",
            event_type="remediation_complete",
            detail=(
                f"Copilot Autofix complete on {branch_name}: {completed} fixed, "
                f"{failed} failed, {skipped} skipped out of {len(alerts)} alerts"
            ),
            metadata={
                "total_alerts": len(alerts),
                "completed": completed,
                "failed": failed,
                "skipped": skipped,
                "branch": branch_name,
            },
        )
        await recorder.finish()
        recorder_finished = True

        # Fetch all jobs for the response
        placeholders = ",".join("?" * len(request.alert_numbers))
        cursor = await db.execute(
            f"""SELECT * FROM copilot_autofix_jobs
                WHERE repo = ? AND alert_number IN ({placeholders})
                ORDER BY created_at DESC""",
            (resolved_repo, *tuple(request.alert_numbers)),
        )
        rows = await cursor.fetchall()
        jobs = [
            CopilotAutofixJob(
                id=row["id"],
                alert_number=row["alert_number"],
                rule_id=row["rule_id"],
                file_path=row["file_path"],
                status=row["status"],
                autofix_status=row["autofix_status"],
                commit_sha=row["commit_sha"],
                description=row["description"],
                error_message=row["error_message"],
                created_at=row["created_at"],
                updated_at=row["updated_at"],
            )
            for row in rows
        ]

        return CopilotAutofixResponse(
            total_alerts=len(alerts),
            completed=completed,
            failed=failed,
            skipped=skipped,
            jobs=jobs,
            message=(
                f"Copilot Autofix complete on {branch_name}: "
                f"{completed} fixed, {failed} failed, {skipped} skipped"
            ),
        )
    except Exception:
        if not recorder_finished:
            try:
                await recorder.finish("failed")
            except Exception:
                logger.exception("Failed to mark replay run as failed")
        raise
    finally:
        await db.close()


# ---------------------------------------------------------------------------
# Benchmark — run ALL tools simultaneously
# ---------------------------------------------------------------------------

ALL_TOOLS = ["devin", "copilot", "anthropic", "openai", "gemini"]

INTER_TOOL_DELAY = 1.0  # seconds between launching tool tasks (rate-limit friendly)


async def _benchmark_api_tool(
    tool: str,
    run_id: int,
    alerts: list[Alert],
    resolved_repo: str,
    baseline_branch: str,
) -> None:
    """Background task: run API-tool remediation and record to shared run."""
    key_attr = _API_TOOL_CONFIG.get(tool)
    if not key_attr or not getattr(settings, key_attr, None):
        recorder = await ReplayRecorder.attach(run_id, [tool], resolved_repo)
        await recorder.record(
            tool=tool,
            event_type="error",
            detail=f"{tool} API key not configured — skipping",
        )
        return

    github = GitHubClient(repo=resolved_repo)
    branch_name = f"remediate/{tool}-bench-{int(_time.time())}"

    try:
        await github.create_branch(branch_name, from_branch=baseline_branch)
    except Exception as e:
        recorder = await ReplayRecorder.attach(run_id, [tool], resolved_repo)
        await recorder.record(
            tool=tool,
            event_type="error",
            detail=f"Failed to create branch {branch_name}: {e}",
        )
        return

    file_groups = _group_alerts_by_file(alerts)

    recorder = await ReplayRecorder.attach(run_id, [tool], resolved_repo)
    await recorder.record(
        tool=tool,
        event_type="scan_started",
        detail=(
            f"Starting {tool} remediation for {len(alerts)} alerts "
            f"across {len(file_groups)} files on {branch_name}"
        ),
        metadata={
            "repo": resolved_repo,
            "branch": branch_name,
            "source_branch": baseline_branch,
            "alert_count": len(alerts),
            "file_count": len(file_groups),
            "tool": tool,
        },
    )

    db = await get_db()
    completed = 0
    failed = 0

    try:
        for file_path, file_alerts in file_groups.items():
            try:
                await recorder.record(
                    tool=tool,
                    event_type="alert_triaged",
                    detail=f"Fetching {file_path} for {len(file_alerts)} alert(s)",
                    alert_number=file_alerts[0].number,
                    metadata={
                        "file_path": file_path,
                        "alert_count": len(file_alerts),
                        "alert_numbers": [a.number for a in file_alerts],
                    },
                )
                file_content = await github.get_file_content(file_path, branch_name)

                # Build prompt
                if len(file_alerts) == 1:
                    alert = file_alerts[0]
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
                else:
                    prompt = build_grouped_prompt_for_file(
                        file_path=file_path,
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
                            for a in file_alerts
                        ],
                    )

                prompt_tokens = count_tokens(prompt)
                await recorder.record(
                    tool=tool,
                    event_type="api_call_sent",
                    detail=f"Sending {len(file_alerts)} alert(s) for {file_path} to {tool}",
                    alert_number=file_alerts[0].number,
                    metadata={
                        "prompt_tokens": prompt_tokens,
                        "file_path": file_path,
                        "alert_count": len(file_alerts),
                    },
                )

                llm_result = await call_llm_with_delay(tool, prompt)

                if not llm_result.extracted_code or not llm_result.extracted_code.strip():
                    raise ValueError("LLM returned empty response")

                call_cost = compute_llm_call_cost(
                    tool, llm_result.input_tokens, llm_result.output_tokens,
                )

                await recorder.record(
                    tool=tool,
                    event_type="patch_generated",
                    detail=f"{llm_result.model} generated fix for {len(file_alerts)} alert(s) in {file_path}",
                    alert_number=file_alerts[0].number,
                    metadata={
                        "model": llm_result.model,
                        "latency_ms": llm_result.latency_ms,
                        "input_tokens": llm_result.input_tokens,
                        "output_tokens": llm_result.output_tokens,
                        "file_path": file_path,
                    },
                    cost_usd=call_cost,
                )

                # Commit fix
                alert_refs = ", ".join(f"#{a.number}" for a in file_alerts)
                commit_msg = (
                    f"fix: remediate {len(file_alerts)} alert(s) "
                    f"({alert_refs}) in {file_path} via {tool}"
                )
                commit_sha = await github.update_file_content(
                    path=file_path,
                    new_content=llm_result.extracted_code,
                    branch=branch_name,
                    commit_message=commit_msg,
                )

                await recorder.record(
                    tool=tool,
                    event_type="patch_applied",
                    detail=f"Patch committed to {branch_name} for {file_path}",
                    alert_number=file_alerts[0].number,
                    metadata={
                        "commit_sha": commit_sha,
                        "branch": branch_name,
                        "file_path": file_path,
                    },
                )
                completed += len(file_alerts)

            except Exception as e:
                logger.exception("Benchmark %s: failed to remediate %s", tool, file_path)
                await recorder.record(
                    tool=tool,
                    event_type="error",
                    detail=f"Failed to remediate {file_path}: {str(e)[:200]}",
                    alert_number=file_alerts[0].number,
                    metadata={"error": str(e)[:500], "file_path": file_path},
                )
                failed += len(file_alerts)

        await recorder.record(
            tool=tool,
            event_type="remediation_complete",
            detail=(
                f"{tool} complete: {completed} fixed, {failed} failed "
                f"out of {len(alerts)} alerts"
            ),
            metadata={
                "completed": completed,
                "failed": failed,
                "total_alerts": len(alerts),
                "tool": tool,
                "branch": branch_name,
            },
        )
    except Exception:
        logger.exception("Benchmark %s task failed", tool)
    finally:
        await db.close()


async def _benchmark_devin(
    run_id: int,
    alerts: list[Alert],
    resolved_repo: str,
    baseline_branch: str,
) -> None:
    """Background task: run Devin remediation and record to shared run."""
    if not settings.devin_api_key:
        recorder = await ReplayRecorder.attach(run_id, ["devin"], resolved_repo)
        await recorder.record(
            tool="devin",
            event_type="error",
            detail="DEVIN_API_KEY not configured — skipping",
        )
        return

    github = GitHubClient(repo=resolved_repo)
    devin = DevinClient()
    branch_name = f"remediate/devin-bench-{int(_time.time())}"

    try:
        await github.create_branch(branch_name, from_branch=baseline_branch)
    except Exception as e:
        recorder = await ReplayRecorder.attach(run_id, ["devin"], resolved_repo)
        await recorder.record(
            tool="devin",
            event_type="error",
            detail=f"Failed to create branch {branch_name}: {e}",
        )
        return

    file_groups = _group_alerts_by_file(alerts)

    recorder = await ReplayRecorder.attach(run_id, ["devin"], resolved_repo)
    await recorder.record(
        tool="devin",
        event_type="scan_started",
        detail=(
            f"Starting Devin remediation for {len(alerts)} alerts "
            f"across {len(file_groups)} files on {branch_name}"
        ),
        metadata={
            "repo": resolved_repo,
            "branch": branch_name,
            "alert_count": len(alerts),
            "file_count": len(file_groups),
        },
    )

    db = await get_db()
    sessions_created = 0

    try:
        for file_path, file_alerts in file_groups.items():
            try:
                await recorder.record(
                    tool="devin",
                    event_type="session_created",
                    detail=f"Creating Devin session for {len(file_alerts)} alert(s) in {file_path}",
                    alert_number=file_alerts[0].number,
                    metadata={
                        "file_path": file_path,
                        "alert_count": len(file_alerts),
                        "alert_numbers": [a.number for a in file_alerts],
                        "branch": branch_name,
                    },
                )

                if len(file_alerts) == 1:
                    result = await devin.create_remediation_session(
                        file_alerts[0], resolved_repo, branch_name,
                    )
                else:
                    result = await devin.create_grouped_session(
                        file_alerts, resolved_repo, branch_name,
                    )
                session_id = result.get("session_id", "")

                for alert in file_alerts:
                    await db.execute(
                        """INSERT INTO devin_sessions
                           (repo, session_id, alert_number, rule_id, file_path, status)
                           VALUES (?, ?, ?, ?, ?, 'running')""",
                        (resolved_repo, session_id, alert.number, alert.rule_id, alert.file_path),
                    )
                await db.commit()

                await recorder.record(
                    tool="devin",
                    event_type="analyzing",
                    detail=f"Devin session {session_id} started for {len(file_alerts)} alert(s) in {file_path}",
                    alert_number=file_alerts[0].number,
                    metadata={
                        "session_id": session_id,
                        "file_path": file_path,
                        "branch": branch_name,
                    },
                )
                sessions_created += 1

            except Exception as e:
                logger.exception("Benchmark devin: failed for %s", file_path)
                await recorder.record(
                    tool="devin",
                    event_type="error",
                    detail=f"Failed to create session for {file_path}: {str(e)[:200]}",
                    alert_number=file_alerts[0].number,
                    metadata={"error": str(e)[:500], "file_path": file_path},
                )

        await recorder.record(
            tool="devin",
            event_type="remediation_complete",
            detail=(
                f"Devin: created {sessions_created} session(s) for "
                f"{len(alerts)} alerts across {len(file_groups)} files"
            ),
            metadata={
                "sessions_created": sessions_created,
                "total_alerts": len(alerts),
                "file_count": len(file_groups),
                "branch": branch_name,
            },
        )
    except Exception:
        logger.exception("Benchmark devin task failed")
    finally:
        await db.close()


async def _benchmark_copilot(
    run_id: int,
    alerts: list[Alert],
    resolved_repo: str,
    baseline_branch: str,
) -> None:
    """Background task: run Copilot Autofix and record to shared run."""
    github = GitHubClient(repo=resolved_repo)
    branch_name = f"remediate/copilot-bench-{int(_time.time())}"

    try:
        await github.create_branch(branch_name, from_branch=baseline_branch)
    except Exception as e:
        recorder = await ReplayRecorder.attach(run_id, ["copilot"], resolved_repo)
        await recorder.record(
            tool="copilot",
            event_type="error",
            detail=f"Failed to create branch {branch_name}: {e}",
        )
        return

    recorder = await ReplayRecorder.attach(run_id, ["copilot"], resolved_repo)
    await recorder.record(
        tool="copilot",
        event_type="scan_started",
        detail=f"Starting Copilot Autofix for {len(alerts)} alerts on {branch_name}",
        metadata={
            "repo": resolved_repo,
            "branch": branch_name,
            "alert_count": len(alerts),
        },
    )

    db = await get_db()
    completed = 0
    failed = 0

    try:
        for idx, alert in enumerate(alerts):
            # Rate limiting between triggers
            if idx > 0:
                await asyncio.sleep(COPILOT_INTER_ALERT_DELAY)

            try:
                await recorder.record(
                    tool="copilot",
                    event_type="autofix_triggered",
                    detail=f"Triggering Copilot Autofix for alert #{alert.number} ({alert.rule_id})",
                    alert_number=alert.number,
                    metadata={
                        "rule_id": alert.rule_id,
                        "file_path": alert.file_path,
                        "severity": alert.severity,
                    },
                    cost_usd=COPILOT_COST_PER_REQUEST,
                )

                autofix = await github.poll_autofix(alert.number)
                autofix_status = autofix.get("status", "unknown")

                if autofix_status == "succeeded":
                    commit_msg = (
                        f"fix: Copilot Autofix for alert #{alert.number} "
                        f"({alert.rule_id}) in {alert.file_path}"
                    )
                    commit_result = await github.commit_autofix(
                        alert.number, branch_name, commit_msg,
                    )
                    commit_sha = commit_result.get("sha", "")

                    await recorder.record(
                        tool="copilot",
                        event_type="patch_applied",
                        detail=f"Copilot fix committed for alert #{alert.number}",
                        alert_number=alert.number,
                        metadata={
                            "commit_sha": commit_sha,
                            "branch": branch_name,
                            "file_path": alert.file_path,
                        },
                    )
                    completed += 1
                else:
                    await recorder.record(
                        tool="copilot",
                        event_type="autofix_result",
                        detail=f"Autofix for alert #{alert.number}: {autofix_status}",
                        alert_number=alert.number,
                        metadata={"autofix_status": autofix_status},
                    )
                    failed += 1

            except Exception as e:
                logger.exception("Benchmark copilot: failed for alert #%d", alert.number)
                await recorder.record(
                    tool="copilot",
                    event_type="error",
                    detail=f"Failed autofix for alert #{alert.number}: {str(e)[:200]}",
                    alert_number=alert.number,
                    metadata={"error": str(e)[:500]},
                )
                failed += 1

        await recorder.record(
            tool="copilot",
            event_type="remediation_complete",
            detail=f"Copilot complete: {completed} fixed, {failed} failed out of {len(alerts)} alerts",
            metadata={
                "completed": completed,
                "failed": failed,
                "total_alerts": len(alerts),
                "branch": branch_name,
            },
        )
    except Exception:
        logger.exception("Benchmark copilot task failed")
    finally:
        await db.close()


async def _run_benchmark_tasks(
    run_id: int,
    alerts: list[Alert],
    resolved_repo: str,
    baseline_branch: str,
    tools: list[str],
) -> None:
    """Orchestrate all benchmark tool tasks with rate-limit-aware staggering."""
    tasks: list[asyncio.Task[None]] = []

    for i, tool in enumerate(tools):
        # Stagger tool launches to be rate-limit friendly
        if i > 0:
            await asyncio.sleep(INTER_TOOL_DELAY)

        if tool == "devin":
            task = asyncio.create_task(
                _benchmark_devin(run_id, alerts, resolved_repo, baseline_branch)
            )
        elif tool == "copilot":
            task = asyncio.create_task(
                _benchmark_copilot(run_id, alerts, resolved_repo, baseline_branch)
            )
        elif tool in _API_TOOL_CONFIG:
            task = asyncio.create_task(
                _benchmark_api_tool(tool, run_id, alerts, resolved_repo, baseline_branch)
            )
        else:
            continue
        tasks.append(task)

    # Wait for all tools to finish
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)

    # Mark the shared run as completed
    db = await get_db()
    try:
        now = datetime.now(timezone.utc).isoformat()
        await db.execute(
            "UPDATE replay_runs SET status = 'completed', ended_at = ? WHERE id = ?",
            (now, run_id),
        )
        await db.commit()
    finally:
        await db.close()


@router.post("/benchmark", response_model=BenchmarkResponse)
async def trigger_benchmark(
    request: BenchmarkRequest,
    background_tasks: BackgroundTasks,
    repo: str | None = Query(default=None, description="Repository (owner/repo)"),
) -> BenchmarkResponse:
    """Launch a benchmark that runs ALL remediation tools on filtered alerts.

    Alerts are filtered by the requested severities (default: all).
    A shared replay run is created so the frontend can poll for live progress.
    Each tool runs as a background task with its own branch.
    """
    resolved_repo = await resolve_repo(repo)
    baseline_branch = await resolve_baseline_branch(resolved_repo)

    github = GitHubClient(repo=resolved_repo)
    all_alerts = await github.get_alerts(baseline_branch, state="open")

    # Filter by selected severities
    severity_set = set(s.lower() for s in request.severities)
    alerts = [a for a in all_alerts if a.severity.lower() in severity_set]

    if not alerts:
        raise HTTPException(
            status_code=404,
            detail="No open alerts matching the selected severities.",
        )

    # Count alerts per severity
    severity_counts: dict[str, int] = {}
    for a in alerts:
        sev = a.severity.lower()
        severity_counts[sev] = severity_counts.get(sev, 0) + 1

    # Determine which tools to run
    tools = list(ALL_TOOLS)

    # Create the shared replay run
    now = datetime.now(timezone.utc).isoformat()
    db = await get_db()
    try:
        cursor = await db.execute(
            "INSERT INTO replay_runs"
            " (repo, scan_id, started_at, status, tools, total_cost_usd)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (resolved_repo, None, now, "running", json.dumps(tools), 0.0),
        )
        run_id = cursor.lastrowid
        assert run_id is not None
        await db.commit()
    finally:
        await db.close()

    # Launch all tool tasks in the background
    background_tasks.add_task(
        _run_benchmark_tasks, run_id, alerts, resolved_repo, baseline_branch, tools
    )

    return BenchmarkResponse(
        run_id=run_id,
        alert_count=len(alerts),
        severity_counts=severity_counts,
        tools=tools,
        message=(
            f"Benchmark started: {len(alerts)} alerts across {len(tools)} tools. "
            f"Track progress at run_id={run_id}."
        ),
    )
