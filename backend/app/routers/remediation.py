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
    SpotBugsResultsResponse,
    SpotBugsToolResult,
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
from app.services.repo_resolver import (
    get_latest_tool_branches,
    resolve_baseline_branch,
    resolve_repo,
)
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
    if not settings.devin_api_key or not settings.devin_org_id:
        raise HTTPException(status_code=400, detail="DEVIN_API_KEY and DEVIN_ORG_ID must be configured")

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
    if not settings.devin_api_key or not settings.devin_org_id:
        raise HTTPException(status_code=400, detail="DEVIN_API_KEY and DEVIN_ORG_ID must be configured")

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

        if not rows:
            return {"updated": 0, "total_running": 0}

        # Fetch all org sessions once (includes status_detail) rather
        # than hitting the single-session endpoint per row.
        try:
            all_org_sessions = await devin.list_sessions()
            org_sessions_by_id = {
                s["session_id"]: s for s in all_org_sessions
            }
        except Exception:
            logger.exception("Failed to list org sessions, falling back to per-session polling")
            org_sessions_by_id = {}

        for row in rows:
            try:
                sid = row["session_id"]
                status_data = org_sessions_by_id.get(sid)
                if status_data is None:
                    # Fallback to single-session endpoint
                    status_data = await devin.get_session_status(sid)

                # Use _is_devin_session_done to also detect waiting_for_user
                _done, effective_status = _is_devin_session_done(status_data)
                new_status = effective_status if _done else status_data.get("status", "unknown")

                prs = status_data.get("pull_requests", [])
                pr_url = prs[0].get("pr_url") if prs else None
                acus = status_data.get("acus_consumed")

                await db.execute(
                    """UPDATE devin_sessions
                       SET status = ?, pr_url = ?, acus = COALESCE(?, acus), updated_at = datetime('now')
                       WHERE repo = ? AND session_id = ? AND file_path = ?""",
                    (new_status, pr_url, acus, row["repo"], sid, row["file_path"]),
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
                                "raw_response": autofix,
                            },
                        )

                        if autofix_status not in ("succeeded", "success"):
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
                                "raw_response": autofix,
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

# CodeQL readiness polling configuration
CODEQL_POLL_INTERVAL = 30.0  # seconds between CodeQL readiness checks
CODEQL_MAX_WAIT = 20 * 60  # 20 minutes max wait for CodeQL analysis

# Devin session polling configuration
DEVIN_POLL_INTERVAL = 30.0  # seconds between Devin session status checks
DEVIN_MAX_WAIT = 30 * 60   # 30 minutes max wait for a single Devin session
DEVIN_TERMINAL_STATES = {"exit", "error", "suspended"}
# status_detail values that indicate the session is effectively done
# (e.g. "waiting_for_user" means Devin finished its work and is waiting for
# human input which won't come in an automated benchmark)
DEVIN_TERMINAL_STATUS_DETAILS = {"waiting_for_user"}

# In-memory cancel events for running benchmarks (run_id -> asyncio.Event)
_cancel_events: dict[int, asyncio.Event] = {}


async def _benchmark_api_tool(
    tool: str,
    run_id: int,
    alerts: list[Alert],
    resolved_repo: str,
    baseline_branch: str,
    start_time: float | None = None,
    branch_name: str | None = None,
    cancel_event: asyncio.Event | None = None,
) -> None:
    """Background task: run API-tool remediation and record to shared run."""
    key_attr = _API_TOOL_CONFIG.get(tool)
    if not key_attr or not getattr(settings, key_attr, None):
        recorder = await ReplayRecorder.attach(run_id, [tool], resolved_repo, start_time=start_time)
        await recorder.record(
            tool=tool,
            event_type="error",
            detail=f"{tool} API key not configured — skipping",
        )
        return

    github = GitHubClient(repo=resolved_repo)

    # Use pre-created branch if provided, otherwise create one (legacy path)
    if branch_name is None:
        branch_name = f"remediate/{tool}-bench-{int(_time.time())}"
        try:
            await github.create_branch(branch_name, from_branch=baseline_branch)
        except Exception as e:
            recorder = await ReplayRecorder.attach(run_id, [tool], resolved_repo, start_time=start_time)
            await recorder.record(
                tool=tool,
                event_type="error",
                detail=f"Failed to create branch {branch_name}: {e}",
            )
            return

    file_groups = _group_alerts_by_file(alerts)

    recorder = await ReplayRecorder.attach(run_id, [tool], resolved_repo, start_time=start_time)
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
            # Check for cancellation before each file group
            if cancel_event and cancel_event.is_set():
                await recorder.record(
                    tool=tool,
                    event_type="cancelled",
                    detail=f"{tool} cancelled after {completed} fixed, {failed} failed",
                    metadata={"completed": completed, "failed": failed},
                )
                break

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
                        "raw_response": llm_result.raw_response_text[:5000],
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


def _is_devin_session_done(status_data: dict) -> tuple[bool, str]:
    """Return (is_done, effective_status) for a Devin session response.

    A session is considered done when:
    - ``status`` is in DEVIN_TERMINAL_STATES (exit/error/suspended), OR
    - ``status_detail`` is in DEVIN_TERMINAL_STATUS_DETAILS (e.g.
      "waiting_for_user" — means Devin finished its work and is waiting
      for human input that won't come in an automated benchmark).
    """
    status = status_data.get("status", "unknown")
    if status in DEVIN_TERMINAL_STATES:
        return True, status
    status_detail = status_data.get("status_detail", "")
    if status_detail in DEVIN_TERMINAL_STATUS_DETAILS:
        return True, f"{status}:{status_detail}"
    return False, status


async def _benchmark_devin(
    run_id: int,
    alerts: list[Alert],
    resolved_repo: str,
    baseline_branch: str,
    start_time: float | None = None,
    branch_name: str | None = None,
    cancel_event: asyncio.Event | None = None,
) -> None:
    """Background task: run Devin remediation using a **single session**.

    To avoid rate limits, only ONE Devin session is created.  Each file
    group's alerts are sent as follow-up messages to the same session once
    Devin reaches ``waiting_for_user`` (i.e. finished the previous task).

    Flow:
    1. Create a single session with the first file group
    2. Poll until ``waiting_for_user`` or a hard terminal state
    3. Detect new commits, record events
    4. Send the next file group as a message to the same session
    5. Repeat 2-4 until all file groups are done
    """
    if not settings.devin_api_key or not settings.devin_org_id:
        recorder = await ReplayRecorder.attach(run_id, ["devin"], resolved_repo, start_time=start_time)
        await recorder.record(
            tool="devin",
            event_type="error",
            detail="DEVIN_API_KEY or DEVIN_ORG_ID not configured — skipping",
        )
        return

    github = GitHubClient(repo=resolved_repo)
    devin = DevinClient()

    # Use pre-created branch if provided, otherwise create one (legacy path)
    if branch_name is None:
        branch_name = f"remediate/devin-bench-{int(_time.time())}"
        try:
            await github.create_branch(branch_name, from_branch=baseline_branch)
        except Exception as e:
            recorder = await ReplayRecorder.attach(run_id, ["devin"], resolved_repo, start_time=start_time)
            await recorder.record(
                tool="devin",
                event_type="error",
                detail=f"Failed to create branch {branch_name}: {e}",
            )
            return

    file_groups = _group_alerts_by_file(alerts)

    recorder = await ReplayRecorder.attach(run_id, ["devin"], resolved_repo, start_time=start_time)
    await recorder.record(
        tool="devin",
        event_type="scan_started",
        detail=(
            f"Starting Devin remediation for {len(alerts)} alerts "
            f"across {len(file_groups)} files on {branch_name} "
            f"(single session — alerts sent as follow-up messages)"
        ),
        metadata={
            "repo": resolved_repo,
            "branch": branch_name,
            "alert_count": len(alerts),
            "file_count": len(file_groups),
        },
    )

    db = await get_db()
    total_commits = 0
    failed = 0
    session_id = ""
    session_url = ""
    # Track per-file-group info for the UI
    all_sessions: list[dict] = []  # {session_id, file_path, status, url}

    try:
        # Capture branch HEAD before Devin pushes any commits
        last_known_sha = await github.get_branch_sha(branch_name)

        file_group_items = list(file_groups.items())

        for idx, (file_path, file_alerts) in enumerate(file_group_items):
            # Check for cancellation before each task
            if cancel_event and cancel_event.is_set():
                await recorder.record(
                    tool="devin",
                    event_type="cancelled",
                    detail=f"Devin cancelled after {idx}/{len(file_group_items)} file group(s)",
                    metadata={"groups_completed": idx},
                )
                break

            # -- Step 1: Create session (first group) or send message (subsequent) --
            # Guard: if session creation failed at idx=0, can't send messages
            if idx > 0 and not session_id:
                logger.warning(
                    "Benchmark %d: no session_id — skipping remaining %d group(s)",
                    run_id, len(file_group_items) - idx,
                )
                failed += sum(len(fa) for _, fa in file_group_items[idx:])
                break

            try:
                if idx == 0:
                    # Create the single session with the first file group
                    await recorder.record(
                        tool="devin",
                        event_type="session_created",
                        detail=(
                            f"[{idx + 1}/{len(file_group_items)}] Creating Devin session "
                            f"for {len(file_alerts)} alert(s) in {file_path}"
                        ),
                        alert_number=file_alerts[0].number,
                        metadata={
                            "file_path": file_path,
                            "alert_count": len(file_alerts),
                            "alert_numbers": [a.number for a in file_alerts],
                            "branch": branch_name,
                            "group_index": idx + 1,
                            "total_groups": len(file_group_items),
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
                    session_url = result.get("url", f"https://app.devin.ai/sessions/{session_id}")

                    for alert in file_alerts:
                        await db.execute(
                            """INSERT INTO devin_sessions
                               (repo, session_id, alert_number, rule_id, file_path, status)
                               VALUES (?, ?, ?, ?, ?, 'running')""",
                            (resolved_repo, session_id, alert.number, alert.rule_id, alert.file_path),
                        )
                    await db.commit()

                else:
                    # Send the next file group as a follow-up message
                    followup = devin.build_followup_message(
                        file_alerts, resolved_repo, branch_name,
                    )
                    await devin.send_message(session_id, followup)

                    # Record DB rows for the new alert group (same session)
                    for alert in file_alerts:
                        await db.execute(
                            """INSERT OR IGNORE INTO devin_sessions
                               (repo, session_id, alert_number, rule_id, file_path, status)
                               VALUES (?, ?, ?, ?, ?, 'running')""",
                            (resolved_repo, session_id, alert.number, alert.rule_id, alert.file_path),
                        )
                    await db.commit()

                    await recorder.record(
                        tool="devin",
                        event_type="message_sent",
                        detail=(
                            f"[{idx + 1}/{len(file_group_items)}] Sent follow-up message "
                            f"for {len(file_alerts)} alert(s) in {file_path}"
                        ),
                        alert_number=file_alerts[0].number,
                        metadata={
                            "session_id": session_id,
                            "session_url": session_url,
                            "file_path": file_path,
                            "alert_count": len(file_alerts),
                            "alert_numbers": [a.number for a in file_alerts],
                            "group_index": idx + 1,
                            "total_groups": len(file_group_items),
                        },
                    )

                # Track this file group in the UI list
                all_sessions.append({
                    "session_id": session_id,
                    "file_path": file_path,
                    "status": "running",
                    "url": session_url,
                })

                if idx == 0:
                    await recorder.record(
                        tool="devin",
                        event_type="analyzing",
                        detail=(
                            f"[{idx + 1}/{len(file_group_items)}] Devin session started "
                            f"for {file_path}"
                        ),
                        alert_number=file_alerts[0].number,
                        metadata={
                            "session_id": session_id,
                            "session_url": session_url,
                            "file_path": file_path,
                            "branch": branch_name,
                        },
                    )

            except Exception as e:
                logger.exception("Benchmark devin: failed to create/message session for %s", file_path)
                await recorder.record(
                    tool="devin",
                    event_type="error",
                    detail=f"Failed to create/message session for {file_path}: {str(e)[:200]}",
                    alert_number=file_alerts[0].number,
                    metadata={"error": str(e)[:500], "file_path": file_path},
                )
                failed += len(file_alerts)
                continue

            # -- Step 2: Poll until waiting_for_user or hard terminal --
            poll_start = _time.monotonic()
            task_done = False
            effective_status = "unknown"

            while not task_done:
                if cancel_event and cancel_event.is_set():
                    for s in all_sessions:
                        if s["file_path"] == file_path:
                            s["status"] = "cancelled"
                    await db.execute(
                        """UPDATE devin_sessions
                           SET status = 'cancelled', updated_at = datetime('now')
                           WHERE repo = ? AND session_id = ? AND file_path = ?""",
                        (resolved_repo, session_id, file_path),
                    )
                    await db.commit()
                    await recorder.record(
                        tool="devin",
                        event_type="cancelled",
                        detail=f"Cancelled while polling session {session_id} for {file_path}",
                        metadata={"session_id": session_id, "file_path": file_path},
                    )
                    break

                elapsed = _time.monotonic() - poll_start
                if elapsed > DEVIN_MAX_WAIT:
                    logger.warning(
                        "Benchmark %d: Devin session %s timed out after %.0fs for %s",
                        run_id, session_id, elapsed, file_path,
                    )
                    for s in all_sessions:
                        if s["file_path"] == file_path:
                            s["status"] = "timeout"
                    await db.execute(
                        """UPDATE devin_sessions
                           SET status = 'timeout', updated_at = datetime('now')
                           WHERE repo = ? AND session_id = ? AND file_path = ?""",
                        (resolved_repo, session_id, file_path),
                    )
                    await db.commit()
                    await recorder.record(
                        tool="devin",
                        event_type="polling_timeout",
                        detail=(
                            f"Session {session_id} timed out after "
                            f"{int(elapsed)}s for {file_path}"
                        ),
                        alert_number=file_alerts[0].number,
                        metadata={
                            "session_id": session_id,
                            "elapsed_s": int(elapsed),
                            "file_path": file_path,
                        },
                    )
                    failed += len(file_alerts)
                    break

                await asyncio.sleep(DEVIN_POLL_INTERVAL)

                try:
                    # Use list_sessions which reliably returns status_detail
                    all_org_sessions = await devin.list_sessions()
                    status_data = next(
                        (s for s in all_org_sessions if s.get("session_id") == session_id),
                        None,
                    )
                    if status_data is None:
                        status_data = await devin.get_session_status(session_id)

                    status = status_data.get("status", "unknown")
                    status_detail = status_data.get("status_detail", "")

                    # Hard terminal states — session is completely done
                    if status in DEVIN_TERMINAL_STATES:
                        task_done = True
                        effective_status = status
                    # waiting_for_user — Devin finished this task, ready for next
                    elif status_detail in DEVIN_TERMINAL_STATUS_DETAILS:
                        task_done = True
                        effective_status = f"{status}:{status_detail}"
                    else:
                        continue  # Still running, keep polling

                    acus = status_data.get("acus_consumed")
                    cost = compute_devin_session_cost(acus) if acus else 0.0
                    session_url = status_data.get("url", session_url)

                    # Update tracker for this file group
                    for s in all_sessions:
                        if s["file_path"] == file_path:
                            s["status"] = effective_status
                            s["url"] = session_url

                    await recorder.record(
                        tool="devin",
                        event_type="session_complete",
                        detail=(
                            f"[{idx + 1}/{len(file_group_items)}] Session {session_id} "
                            f"finished ({effective_status}) for {file_path}"
                        ),
                        alert_number=file_alerts[0].number,
                        metadata={
                            "session_id": session_id,
                            "session_url": session_url,
                            "status": effective_status,
                            "file_path": file_path,
                            "acus_consumed": acus,
                            "raw_response": status_data,
                        },
                        cost_usd=cost,
                    )

                    # Update devin_sessions table
                    prs = status_data.get("pull_requests", [])
                    pr_url = prs[0].get("pr_url") if prs else None
                    await db.execute(
                        """UPDATE devin_sessions
                           SET status = ?, pr_url = ?,
                               acus = COALESCE(?, acus),
                               updated_at = datetime('now')
                           WHERE repo = ? AND session_id = ? AND file_path = ?""",
                        (effective_status, pr_url, acus, resolved_repo, session_id, file_path),
                    )
                    await db.commit()

                    # Count as failed if hard terminal error
                    if effective_status in ("error", "suspended"):
                        failed += len(file_alerts)

                except Exception as e:
                    logger.warning(
                        "Benchmark devin: failed to poll session %s: %s",
                        session_id, e,
                    )

            # -- Step 3: Detect new commits from this task --
            # Run BEFORE the hard-terminal break so that commits from
            # the current file group (including on "exit") are recorded.
            try:
                new_commits = await github.list_commits(
                    branch_name, since_sha=last_known_sha,
                )

                if new_commits:
                    # Record one patch_applied per alert in this group
                    # (so 3 alerts = 3 fixes, not 1)
                    for alert in file_alerts:
                        await recorder.record(
                            tool="devin",
                            event_type="patch_applied",
                            detail=(
                                f"Devin fix for alert #{alert.number} "
                                f"({alert.rule_id}) in {file_path}"
                            ),
                            alert_number=alert.number,
                            metadata={
                                "commit_sha": new_commits[0]["sha"],
                                "branch": branch_name,
                                "commit_count": len(new_commits),
                                "session_id": session_id,
                                "file_path": file_path,
                            },
                        )
                    total_commits += len(new_commits)
                    last_known_sha = new_commits[0]["sha"]
                else:
                    logger.info(
                        "Benchmark %d: no new commits from session %s for %s",
                        run_id, session_id, file_path,
                    )

            except Exception as e:
                logger.exception(
                    "Benchmark devin: failed to list commits after %s", file_path,
                )
                await recorder.record(
                    tool="devin",
                    event_type="error",
                    detail=f"Failed to check commits for {file_path}: {str(e)[:200]}",
                    metadata={"error": str(e)[:500], "session_id": session_id},
                )

            # If cancelled or hard terminal, stop processing further groups
            if cancel_event and cancel_event.is_set():
                break
            if session_id and effective_status in ("error", "suspended", "exit", "unknown"):
                # Session ended for real or timed out — can't send more messages
                logger.warning(
                    "Benchmark %d: Devin session %s reached terminal/timeout (%s), "
                    "stopping at group %d/%d",
                    run_id, session_id, effective_status, idx + 1, len(file_group_items),
                )
                failed += sum(
                    len(fa) for _, fa in file_group_items[idx + 1:]
                )
                break

        await recorder.record(
            tool="devin",
            event_type="remediation_complete",
            detail=(
                f"Devin complete: 1 session, "
                f"{total_commits} commit(s), {failed} failed "
                f"out of {len(alerts)} alerts"
            ),
            metadata={
                "session_id": session_id,
                "session_url": session_url,
                "commits": total_commits,
                "failed": failed,
                "total_alerts": len(alerts),
                "file_count": len(file_groups),
                "branch": branch_name,
                "sessions": all_sessions,
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
    start_time: float | None = None,
    branch_name: str | None = None,
    cancel_event: asyncio.Event | None = None,
) -> None:
    """Background task: run Copilot Autofix and record to shared run."""
    github = GitHubClient(repo=resolved_repo)

    # Use pre-created branch if provided, otherwise create one (legacy path)
    if branch_name is None:
        branch_name = f"remediate/copilot-bench-{int(_time.time())}"
        try:
            await github.create_branch(branch_name, from_branch=baseline_branch)
        except Exception as e:
            recorder = await ReplayRecorder.attach(run_id, ["copilot"], resolved_repo, start_time=start_time)
            await recorder.record(
                tool="copilot",
                event_type="error",
                detail=f"Failed to create branch {branch_name}: {e}",
            )
            return

    recorder = await ReplayRecorder.attach(run_id, ["copilot"], resolved_repo, start_time=start_time)
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
            # Check for cancellation before each alert
            if cancel_event and cancel_event.is_set():
                await recorder.record(
                    tool="copilot",
                    event_type="cancelled",
                    detail=f"Copilot cancelled after {completed} fixed, {failed} failed",
                    metadata={"completed": completed, "failed": failed},
                )
                break

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

                if autofix_status in ("succeeded", "success"):
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
                            "raw_response": autofix,
                        },
                    )
                    completed += 1
                else:
                    await recorder.record(
                        tool="copilot",
                        event_type="autofix_result",
                        detail=f"Autofix for alert #{alert.number}: {autofix_status}",
                        alert_number=alert.number,
                        metadata={
                            "autofix_status": autofix_status,
                            "raw_response": autofix,
                        },
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
    branch_map: dict[str, str] | None = None,
    cancel_event: asyncio.Event | None = None,
) -> None:
    """Orchestrate all benchmark tool tasks with rate-limit-aware staggering.

    Two phases:
    1. Wait for CodeQL to analyze the pre-created branches (branch_map)
    2. Launch tool remediation tasks once branches are ready
    """
    # Capture a single reference time so all tool recorders compute consistent
    # timestamp_offset_ms values relative to the same start.
    import time as _time_mod
    run_start_time = _time_mod.monotonic()

    had_exception = False
    try:
        github = GitHubClient(repo=resolved_repo)

        # ---- Phase 1: Wait for CodeQL readiness on each branch ----
        if branch_map:
            recorder = await ReplayRecorder.attach(
                run_id, tools, resolved_repo, start_time=run_start_time,
            )

            # Get baseline alert count as the target
            baseline_alerts = await github.get_alerts(baseline_branch, state="open")
            baseline_count = len(baseline_alerts)
            logger.info(
                "Benchmark %d: baseline branch '%s' has %d open alerts",
                run_id, baseline_branch, baseline_count,
            )

            await recorder.record(
                tool="benchmark",
                event_type="codeql_waiting",
                detail=(
                    f"Waiting for CodeQL to analyze {len(branch_map)} branches "
                    f"(target: {baseline_count} alerts per branch)"
                ),
                metadata={
                    "baseline_count": baseline_count,
                    "branches": branch_map,
                },
            )

            ready_branches: set[str] = set()
            start_wait = _time_mod.monotonic()

            while len(ready_branches) < len(branch_map):
                # Check for cancellation during polling
                if cancel_event and cancel_event.is_set():
                    await recorder.record(
                        tool="benchmark",
                        event_type="cancelled",
                        detail="Benchmark cancelled during CodeQL wait phase",
                        metadata={"ready_branches": list(ready_branches)},
                    )
                    break

                # Check timeout
                elapsed = _time_mod.monotonic() - start_wait
                if elapsed > CODEQL_MAX_WAIT:
                    not_ready = [
                        t for t in branch_map if t not in ready_branches
                    ]
                    logger.warning(
                        "Benchmark %d: CodeQL wait timed out after %.0fs. "
                        "Not ready: %s",
                        run_id, elapsed, not_ready,
                    )
                    await recorder.record(
                        tool="benchmark",
                        event_type="codeql_timeout",
                        detail=(
                            f"CodeQL wait timed out after {int(elapsed)}s. "
                            f"Proceeding with {len(ready_branches)}/{len(branch_map)} ready."
                        ),
                        metadata={
                            "ready": list(ready_branches),
                            "not_ready": not_ready,
                            "elapsed_s": int(elapsed),
                        },
                    )
                    break

                # Poll each not-yet-ready branch
                for tool_name, branch in branch_map.items():
                    if tool_name in ready_branches:
                        continue
                    try:
                        branch_alerts = await github.get_alerts(branch, state="open")
                        if len(branch_alerts) >= baseline_count:
                            ready_branches.add(tool_name)
                            await recorder.record(
                                tool=tool_name,
                                event_type="codeql_ready",
                                detail=(
                                    f"Branch {branch} ready: "
                                    f"{len(branch_alerts)} alerts (target: {baseline_count})"
                                ),
                                metadata={
                                    "branch": branch,
                                    "alert_count": len(branch_alerts),
                                    "baseline_count": baseline_count,
                                },
                            )
                    except Exception as e:
                        logger.debug(
                            "Benchmark %d: polling %s failed (expected during analysis): %s",
                            run_id, branch, e,
                        )

                if len(ready_branches) < len(branch_map):
                    await asyncio.sleep(CODEQL_POLL_INTERVAL)

            # If cancelled during wait, return early (finally block handles cleanup)
            if cancel_event and cancel_event.is_set():
                return

        # ---- Phase 2: Launch tool remediation tasks ----
        tasks: list[asyncio.Task[None]] = []

        for i, tool in enumerate(tools):
            # Stagger tool launches to be rate-limit friendly
            if i > 0:
                await asyncio.sleep(INTER_TOOL_DELAY)

            tool_branch = branch_map.get(tool) if branch_map else None

            if tool == "devin":
                task = asyncio.create_task(
                    _benchmark_devin(
                        run_id, alerts, resolved_repo, baseline_branch,
                        start_time=run_start_time,
                        branch_name=tool_branch,
                        cancel_event=cancel_event,
                    )
                )
            elif tool == "copilot":
                task = asyncio.create_task(
                    _benchmark_copilot(
                        run_id, alerts, resolved_repo, baseline_branch,
                        start_time=run_start_time,
                        branch_name=tool_branch,
                        cancel_event=cancel_event,
                    )
                )
            elif tool in _API_TOOL_CONFIG:
                task = asyncio.create_task(
                    _benchmark_api_tool(
                        tool, run_id, alerts, resolved_repo, baseline_branch,
                        start_time=run_start_time,
                        branch_name=tool_branch,
                        cancel_event=cancel_event,
                    )
                )
            else:
                continue
            tasks.append(task)

        # Wait for all tools to finish
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    except Exception:
        logger.exception("Benchmark %d: _run_benchmark_tasks failed", run_id)
        had_exception = True
    finally:
        # Always clean up cancel event and mark the run as finished
        _cancel_events.pop(run_id, None)

        if cancel_event and cancel_event.is_set():
            final_status = "cancelled"
        elif had_exception:
            final_status = "failed"
        else:
            final_status = "completed"
        db = await get_db()
        try:
            now = datetime.now(timezone.utc).isoformat()
            # Use 'failed' if we hit an unexpected exception (run was still 'running')
            cursor = await db.execute(
                "SELECT status FROM replay_runs WHERE id = ?", (run_id,),
            )
            row = await cursor.fetchone()
            if row and row["status"] == "running":
                await db.execute(
                    "UPDATE replay_runs SET status = ?, ended_at = ? WHERE id = ?",
                    (final_status, now, run_id),
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
    All branches are created upfront with a shared timestamp, then the
    background task waits for CodeQL to analyze each branch before starting
    remediation.
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

    # Generate a single timestamp for all branches
    bench_ts = int(_time.time())
    branch_map = {tool: f"remediate/{tool}-bench-{bench_ts}" for tool in tools}

    # Create all branches upfront (failures return immediately to the caller)
    for tool_name, branch_name in branch_map.items():
        try:
            await github.create_branch(branch_name, from_branch=baseline_branch)
        except Exception as e:
            raise HTTPException(
                status_code=500,
                detail=f"Failed to create branch {branch_name} for {tool_name}: {e}",
            )

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

    # Create cancel event for this run
    cancel_event = asyncio.Event()
    _cancel_events[run_id] = cancel_event

    # Launch all tool tasks in the background
    background_tasks.add_task(
        _run_benchmark_tasks,
        run_id,
        alerts,
        resolved_repo,
        baseline_branch,
        tools,
        branch_map=branch_map,
        cancel_event=cancel_event,
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


@router.post("/benchmark/{run_id}/cancel")
async def cancel_benchmark(
    run_id: int,
    repo: str | None = Query(default=None, description="Repository (owner/repo)"),
) -> dict[str, str | int]:
    """Cancel a running benchmark by setting its cancel event."""
    cancel_event = _cancel_events.get(run_id)
    if not cancel_event:
        raise HTTPException(
            status_code=404,
            detail=f"No active benchmark found for run_id={run_id}",
        )

    cancel_event.set()

    # Update run status immediately
    db = await get_db()
    try:
        now = datetime.now(timezone.utc).isoformat()
        await db.execute(
            "UPDATE replay_runs SET status = 'cancelled', ended_at = ? WHERE id = ?",
            (now, run_id),
        )
        await db.commit()
    finally:
        await db.close()

    return {"status": "cancelled", "run_id": run_id}


# ------------------------------------------------------------------
# SpotBugs results via GitHub Actions artifacts
# ------------------------------------------------------------------

SPOTBUGS_WORKFLOW_NAME = "SpotBugs Analysis"

# Stagger between GitHub API calls to avoid secondary rate limits
_GH_ACTIONS_STAGGER_S = 1.0


def _count_bugs_in_xml(xml_content: str) -> int:
    """Count <BugInstance> elements in SpotBugs XML output."""
    import xml.etree.ElementTree as ET

    try:
        root = ET.fromstring(xml_content)
        return len(root.findall(".//BugInstance"))
    except ET.ParseError:
        logger.warning("Failed to parse SpotBugs XML; falling back to regex count")
        import re

        return len(re.findall(r"<BugInstance", xml_content))


@router.get("/spotbugs-results", response_model=SpotBugsResultsResponse)
async def get_spotbugs_results(
    repo: str | None = Query(default=None, description="Repository (owner/repo)"),
    run_id: int | None = Query(default=None, description="Benchmark run_id to resolve branches from"),
) -> SpotBugsResultsResponse:
    """Fetch SpotBugs CI results for each tool's remediation branch.

    For each tool branch we:
    1. Find the latest "SpotBugs Analysis" workflow run via GitHub Actions API.
    2. If the run is complete and successful, download the artifact zip and
       extract the SpotBugs XML report.
    3. Parse bug counts from the XML and return everything to the frontend.

    If *run_id* is provided, branches are resolved from that benchmark's
    replay_runs entry.  Otherwise we use ``get_latest_tool_branches``.
    """
    resolved_repo = await resolve_repo(repo)

    # Resolve tool → branch mapping
    if run_id is not None:
        db = await get_db()
        try:
            cursor = await db.execute(
                "SELECT tools, branch_name FROM replay_runs WHERE id = ?",
                (run_id,),
            )
            row = await cursor.fetchone()
        finally:
            await db.close()

        if not row:
            raise HTTPException(status_code=404, detail=f"No replay run found for run_id={run_id}")

        import json as _json

        try:
            tools_list: list[str] = _json.loads(row["tools"] or "[]")
        except Exception:
            tools_list = []

        # Benchmark runs store a single branch_name for all tools, but each
        # tool has its own branch of the form remediate/{tool}-bench-{ts}.
        # We need to look up the branch_name pattern.  The benchmark creates
        # branches like "remediate/{tool}-bench-{ts}" so we can re-derive them
        # from the run's branch pattern.
        branch_name: str | None = row["branch_name"]
        tool_branches: dict[str, str] = {}

        if branch_name:
            # Old single-branch runs — all tools share a branch
            for tool in tools_list:
                if tool == "baseline":
                    continue
                tool_branches[tool] = branch_name
        else:
            # Benchmark runs: look up actual per-tool branches from replay_events
            db2 = await get_db()
            try:
                cursor2 = await db2.execute(
                    "SELECT tool, detail, metadata FROM replay_events "
                    "WHERE run_id = ? AND event_type IN ('scan_started', 'codeql_ready') "
                    "ORDER BY id ASC",
                    (run_id,),
                )
                rows2 = await cursor2.fetchall()
                for r in rows2:
                    tool_name = r["tool"]
                    if tool_name == "baseline" or tool_name == "benchmark":
                        continue
                    try:
                        meta = _json.loads(r["metadata"] or "{}")
                    except Exception:
                        meta = {}
                    branch_val = meta.get("branch", "")
                    if branch_val and tool_name not in tool_branches:
                        tool_branches[tool_name] = branch_val
            finally:
                await db2.close()

        if not tool_branches:
            # Fallback to latest known branches
            tool_branches = await get_latest_tool_branches(resolved_repo)
    else:
        tool_branches = await get_latest_tool_branches(resolved_repo)

    if not tool_branches:
        return SpotBugsResultsResponse(
            repo=resolved_repo,
            results=[],
            message="No tool branches found. Run a benchmark first.",
        )

    # For each tool branch, check GitHub Actions for the SpotBugs workflow
    github = GitHubClient(repo=resolved_repo)
    results: list[SpotBugsToolResult] = []

    for i, (tool_name, branch) in enumerate(sorted(tool_branches.items())):
        if i > 0:
            await asyncio.sleep(_GH_ACTIONS_STAGGER_S)

        try:
            runs = await github.get_workflow_runs_for_branch(
                branch, workflow_name=SPOTBUGS_WORKFLOW_NAME, per_page=5,
            )

            if not runs:
                results.append(SpotBugsToolResult(
                    tool=tool_name,
                    branch=branch,
                    workflow_status="not_found",
                    error="No SpotBugs workflow run found for this branch",
                ))
                continue

            run = runs[0]
            status = run["status"]
            conclusion = run.get("conclusion")

            result = SpotBugsToolResult(
                tool=tool_name,
                branch=branch,
                workflow_status=status,
                workflow_conclusion=conclusion,
                workflow_url=run.get("html_url"),
            )

            # If the run completed successfully, try to download the artifact
            if status == "completed" and conclusion == "success":
                await asyncio.sleep(_GH_ACTIONS_STAGGER_S)
                artifacts = await github.get_run_artifacts(run["id"])

                # Find the spotbugs report artifact
                spotbugs_artifact = None
                for artifact in artifacts:
                    if "spotbugs" in artifact["name"].lower():
                        spotbugs_artifact = artifact
                        break

                if spotbugs_artifact and not spotbugs_artifact.get("expired"):
                    await asyncio.sleep(_GH_ACTIONS_STAGGER_S)
                    files = await github.download_artifact_zip(spotbugs_artifact["id"])

                    # Find the XML report file
                    xml_content: str | None = None
                    for fname, content in files.items():
                        if fname.endswith(".xml"):
                            xml_content = content
                            break

                    if xml_content:
                        result.artifact_downloaded = True
                        result.report_content = xml_content
                        result.bug_count = _count_bugs_in_xml(xml_content)
                    else:
                        # No XML found — return raw file listing
                        result.artifact_downloaded = True
                        all_content = "\n\n".join(
                            f"--- {fname} ---\n{content}"
                            for fname, content in files.items()
                        )
                        result.report_content = all_content
                elif spotbugs_artifact and spotbugs_artifact.get("expired"):
                    result.error = "Artifact has expired"
                else:
                    result.error = "No SpotBugs artifact found in workflow run"

            elif status == "completed" and conclusion == "failure":
                result.error = "SpotBugs workflow failed"

            results.append(result)

        except Exception as e:
            logger.exception("Failed to fetch SpotBugs results for %s/%s", tool_name, branch)
            results.append(SpotBugsToolResult(
                tool=tool_name,
                branch=branch,
                workflow_status="error",
                error=str(e)[:500],
            ))

    # Summary message
    completed = sum(1 for r in results if r.workflow_status == "completed")
    running = sum(1 for r in results if r.workflow_status in ("queued", "in_progress"))
    message = f"{completed}/{len(results)} completed"
    if running > 0:
        message += f", {running} still running"

    return SpotBugsResultsResponse(
        repo=resolved_repo,
        results=results,
        message=message,
    )
