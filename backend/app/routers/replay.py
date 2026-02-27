"""Replay endpoints — record and playback remediation timelines."""

import json
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException

from app.config import settings
from app.models.schemas import ReplayEvent, ReplayRun, ReplayRunWithEvents
from app.services.database import get_db

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/replay", tags=["replay"])


@router.post("/runs", response_model=ReplayRun)
async def create_run(scan_id: int | None = None) -> ReplayRun:
    """Create a new replay run to record remediation events."""
    now = datetime.now(timezone.utc).isoformat()
    tools = ["devin", "copilot", "anthropic", "openai"]

    db = await get_db()
    try:
        cursor = await db.execute(
            "INSERT INTO replay_runs (repo, scan_id, started_at, status, tools) VALUES (?, ?, ?, ?, ?)",
            (settings.github_repo, scan_id, now, "running", json.dumps(tools)),
        )
        run_id = cursor.lastrowid
        assert run_id is not None
        await db.commit()

        return ReplayRun(
            id=run_id,
            repo=settings.github_repo,
            scan_id=scan_id,
            started_at=now,
            ended_at=None,
            status="running",
            tools=tools,
        )
    finally:
        await db.close()


@router.post("/runs/{run_id}/events", response_model=ReplayEvent)
async def add_event(
    run_id: int,
    tool: str,
    event_type: str,
    detail: str = "",
    alert_number: int | None = None,
    timestamp_offset_ms: int = 0,
) -> ReplayEvent:
    """Add an event to a replay run."""
    now = datetime.now(timezone.utc).isoformat()

    db = await get_db()
    try:
        # Verify run exists
        cursor = await db.execute("SELECT id FROM replay_runs WHERE id = ?", (run_id,))
        if not await cursor.fetchone():
            raise HTTPException(status_code=404, detail="Run not found")

        cursor = await db.execute(
            """INSERT INTO replay_events
               (run_id, tool, event_type, detail, alert_number, timestamp_offset_ms, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (run_id, tool, event_type, detail, alert_number, timestamp_offset_ms, now),
        )
        event_id = cursor.lastrowid
        assert event_id is not None
        await db.commit()

        return ReplayEvent(
            id=event_id,
            run_id=run_id,
            tool=tool,
            event_type=event_type,
            detail=detail,
            alert_number=alert_number,
            timestamp_offset_ms=timestamp_offset_ms,
            created_at=now,
        )
    finally:
        await db.close()


@router.post("/runs/{run_id}/complete")
async def complete_run(run_id: int) -> dict:
    """Mark a replay run as completed."""
    now = datetime.now(timezone.utc).isoformat()

    db = await get_db()
    try:
        cursor = await db.execute("SELECT id FROM replay_runs WHERE id = ?", (run_id,))
        if not await cursor.fetchone():
            raise HTTPException(status_code=404, detail="Run not found")

        await db.execute(
            "UPDATE replay_runs SET status = 'completed', ended_at = ? WHERE id = ?",
            (now, run_id),
        )
        await db.commit()
        return {"status": "completed", "ended_at": now}
    finally:
        await db.close()


@router.get("/runs", response_model=list[ReplayRun])
async def list_runs() -> list[ReplayRun]:
    """List all replay runs."""
    db = await get_db()
    try:
        cursor = await db.execute("SELECT * FROM replay_runs ORDER BY started_at DESC")
        rows = await cursor.fetchall()
        return [
            ReplayRun(
                id=row["id"],
                repo=row["repo"],
                scan_id=row["scan_id"],
                started_at=row["started_at"],
                ended_at=row["ended_at"],
                status=row["status"],
                tools=json.loads(row["tools"]),
            )
            for row in rows
        ]
    finally:
        await db.close()


@router.get("/runs/{run_id}", response_model=ReplayRunWithEvents)
async def get_run(run_id: int) -> ReplayRunWithEvents:
    """Get a replay run with all its events for playback."""
    db = await get_db()
    try:
        cursor = await db.execute("SELECT * FROM replay_runs WHERE id = ?", (run_id,))
        run = await cursor.fetchone()
        if not run:
            raise HTTPException(status_code=404, detail="Run not found")

        cursor = await db.execute(
            "SELECT * FROM replay_events WHERE run_id = ? ORDER BY timestamp_offset_ms ASC",
            (run_id,),
        )
        event_rows = await cursor.fetchall()
        events = [
            ReplayEvent(
                id=row["id"],
                run_id=row["run_id"],
                tool=row["tool"],
                event_type=row["event_type"],
                detail=row["detail"],
                alert_number=row["alert_number"],
                timestamp_offset_ms=row["timestamp_offset_ms"],
                created_at=row["created_at"],
            )
            for row in event_rows
        ]

        # Compute total duration
        total_duration_ms = events[-1].timestamp_offset_ms if events else None

        return ReplayRunWithEvents(
            id=run["id"],
            repo=run["repo"],
            scan_id=run["scan_id"],
            started_at=run["started_at"],
            ended_at=run["ended_at"],
            status=run["status"],
            tools=json.loads(run["tools"]),
            events=events,
            total_duration_ms=total_duration_ms,
        )
    finally:
        await db.close()


@router.post("/demo-seed")
async def seed_demo_data() -> dict:
    """Seed a demo replay run with simulated events for presentation purposes.

    Creates a realistic-looking timeline showing Devin fixing alerts much faster
    than Copilot and Anthropic.
    """
    now = datetime.now(timezone.utc).isoformat()
    tools = ["devin", "copilot", "anthropic", "openai"]

    db = await get_db()
    try:
        cursor = await db.execute(
            "INSERT INTO replay_runs (repo, scan_id, started_at, ended_at, status, tools) VALUES (?, ?, ?, ?, ?, ?)",
            (settings.github_repo, None, now, now, "completed", json.dumps(tools)),
        )
        run_id = cursor.lastrowid
        assert run_id is not None

        # Simulated events showing different speeds per tool
        demo_events = [
            # === Scan phase ===
            ("devin", "scan_started", "CodeQL scan detected 47 alerts on baseline", None, 0),
            ("copilot", "scan_started", "CodeQL scan detected 47 alerts on baseline", None, 0),
            ("anthropic", "scan_started", "CodeQL scan detected 47 alerts on baseline", None, 0),

            # === Devin (fast, fully automated) ===
            ("devin", "session_created", "Devin session started for CWE-89 SQL Injection", 12, 2000),
            ("devin", "analyzing", "Analyzing DataSourceRealm.java:142", 12, 5000),
            ("devin", "fix_pushed", "Parameterized query fix committed", 12, 18000),
            ("devin", "codeql_verified", "Alert #12 resolved by CodeQL re-scan", 12, 45000),

            ("devin", "session_created", "Devin session started for CWE-79 XSS", 15, 8000),
            ("devin", "fix_pushed", "Output encoding fix committed", 15, 25000),
            ("devin", "codeql_verified", "Alert #15 resolved", 15, 52000),

            ("devin", "session_created", "Devin session started for CWE-22 Path Traversal", 18, 12000),
            ("devin", "fix_pushed", "Path canonicalization fix committed", 18, 30000),
            ("devin", "codeql_verified", "Alert #18 resolved", 18, 58000),

            ("devin", "batch_complete", "Batch 1 complete: 8 alerts fixed in 2 minutes", None, 120000),
            ("devin", "batch_complete", "Batch 2 complete: 15 alerts fixed in 5 minutes", None, 300000),
            ("devin", "batch_complete", "Batch 3 complete: 12 alerts fixed in 8 minutes", None, 480000),
            ("devin", "batch_complete", "Batch 4 complete: 7 alerts fixed in 12 minutes", None, 720000),
            ("devin", "remediation_complete", "42/47 alerts fixed (89.4%). 5 require manual review.", None, 780000),

            # === Copilot Autofix (slower, requires human acceptance) ===
            ("copilot", "suggestion_created", "Autofix suggestion for SQL Injection", 12, 30000),
            ("copilot", "waiting_human", "Waiting for developer to review and accept", 12, 30500),
            ("copilot", "suggestion_accepted", "Developer accepted fix for alert #12", 12, 600000),
            ("copilot", "codeql_verified", "Alert #12 resolved", 12, 900000),

            ("copilot", "suggestion_created", "Autofix suggestion for XSS", 15, 35000),
            ("copilot", "waiting_human", "Waiting for developer to review", 15, 35500),
            ("copilot", "suggestion_accepted", "Developer accepted fix for alert #15", 15, 1200000),

            ("copilot", "batch_complete", "8 suggestions accepted after 30 min", None, 1800000),
            ("copilot", "batch_complete", "15 suggestions accepted after 1.5 hr", None, 5400000),
            ("copilot", "batch_complete", "5 suggestions accepted after 3 hr", None, 10800000),
            ("copilot", "remediation_complete",
             "28/47 alerts fixed (59.6%). Required manual acceptance.",
             None, 14400000),

            # === Anthropic (medium speed, API-based — claude-opus-4-6) ===
            ("anthropic", "api_call_sent", "Sending alert context to claude-opus-4-6", 12, 5000),
            ("anthropic", "patch_generated", "claude-opus-4-6 generated fix for SQL Injection", 12, 15000),
            ("anthropic", "patch_applied", "Patch applied to tomcat-anthropic branch", 12, 20000),
            ("anthropic", "codeql_verified", "Alert #12 resolved", 12, 65000),

            ("anthropic", "api_call_sent", "Sending alert context for XSS to claude-opus-4-6", 15, 18000),
            ("anthropic", "patch_generated", "claude-opus-4-6 generated fix for XSS vulnerability", 15, 28000),
            ("anthropic", "patch_applied", "Patch applied", 15, 32000),
            ("anthropic", "codeql_verified", "Alert #15 resolved", 15, 78000),

            ("anthropic", "batch_complete", "Batch 1: 10 fixes applied in 5 min", None, 300000),
            ("anthropic", "batch_complete", "Batch 2: 12 fixes applied in 15 min", None, 900000),
            ("anthropic", "batch_complete", "Batch 3: 9 fixes applied in 30 min", None, 1800000),
            ("anthropic", "remediation_complete",
             "31/47 alerts fixed (66.0%). 3 patches failed CodeQL verification.",
             None, 2700000),

            # === OpenAI (API-based — gpt-5.3-codex) ===
            ("openai", "api_call_sent", "Sending alert context to gpt-5.3-codex", 12, 4000),
            ("openai", "patch_generated", "gpt-5.3-codex generated fix for SQL Injection", 12, 12000),
            ("openai", "patch_applied", "Patch applied to tomcat-openai branch", 12, 16000),
            ("openai", "codeql_verified", "Alert #12 resolved", 12, 60000),

            ("openai", "api_call_sent", "Sending alert context for XSS to gpt-5.3-codex", 15, 14000),
            ("openai", "patch_generated", "gpt-5.3-codex generated fix for XSS vulnerability", 15, 24000),
            ("openai", "patch_applied", "Patch applied", 15, 28000),
            ("openai", "codeql_verified", "Alert #15 resolved", 15, 72000),

            ("openai", "batch_complete", "Batch 1: 11 fixes applied in 4 min", None, 240000),
            ("openai", "batch_complete", "Batch 2: 13 fixes applied in 12 min", None, 720000),
            ("openai", "batch_complete", "Batch 3: 8 fixes applied in 25 min", None, 1500000),
            ("openai", "remediation_complete",
             "32/47 alerts fixed (68.1%). 4 patches failed CodeQL verification.",
             None, 2400000),
        ]

        for tool, event_type, detail, alert_num, offset_ms in demo_events:
            await db.execute(
                """INSERT INTO replay_events
                   (run_id, tool, event_type, detail, alert_number, timestamp_offset_ms, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (run_id, tool, event_type, detail, alert_num, offset_ms, now),
            )

        await db.commit()

        return {
            "run_id": run_id,
            "events_created": len(demo_events),
            "message": "Demo replay data seeded successfully",
        }
    finally:
        await db.close()
