import logging

from fastapi import APIRouter, HTTPException

from app.config import settings
from app.models.schemas import DevinSession, RemediationRequest, RemediationResponse
from app.services.database import get_db
from app.services.devin_client import DevinClient
from app.services.github_client import GitHubClient

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/remediate", tags=["remediation"])


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
