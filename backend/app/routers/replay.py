"""Replay endpoints — record and playback remediation timelines."""

import json
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Query

from app.models.schemas import ReplayEvent, ReplayRun, ReplayRunWithEvents
from app.services.database import get_db
from app.services.repo_resolver import resolve_repo

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/replay", tags=["replay"])


@router.post("/runs", response_model=ReplayRun)
async def create_run(
    scan_id: int | None = None,
    repo: str | None = Query(default=None, description="Repository (owner/repo)"),
) -> ReplayRun:
    """Create a new replay run to record remediation events."""
    now = datetime.now(timezone.utc).isoformat()
    tools = ["devin", "copilot", "anthropic", "openai", "gemini"]

    db = await get_db()
    try:
        resolved_repo = await resolve_repo(repo)
        cursor = await db.execute(
            "INSERT INTO replay_runs (repo, scan_id, started_at, status, tools) VALUES (?, ?, ?, ?, ?)",
            (resolved_repo, scan_id, now, "running", json.dumps(tools)),
        )
        run_id = cursor.lastrowid
        assert run_id is not None
        await db.commit()

        return ReplayRun(
            id=run_id,
            repo=resolved_repo,
            scan_id=scan_id,
            started_at=now,
            ended_at=None,
            status="running",
            tools=tools,
            total_cost_usd=0.0,
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
    repo: str | None = Query(default=None, description="Repository (owner/repo)"),
) -> ReplayEvent:
    """Add an event to a replay run."""
    now = datetime.now(timezone.utc).isoformat()

    db = await get_db()
    try:
        # Verify run exists for repo
        resolved_repo = await resolve_repo(repo)
        cursor = await db.execute("SELECT id, repo FROM replay_runs WHERE id = ?", (run_id,))
        run = await cursor.fetchone()
        if not run or run["repo"] != resolved_repo:
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
            metadata={},
            created_at=now,
        )
    finally:
        await db.close()


@router.post("/runs/{run_id}/complete")
async def complete_run(
    run_id: int,
    repo: str | None = Query(default=None, description="Repository (owner/repo)"),
) -> dict:
    """Mark a replay run as completed."""
    now = datetime.now(timezone.utc).isoformat()

    db = await get_db()
    try:
        resolved_repo = await resolve_repo(repo)
        cursor = await db.execute("SELECT id, repo FROM replay_runs WHERE id = ?", (run_id,))
        run = await cursor.fetchone()
        if not run or run["repo"] != resolved_repo:
            raise HTTPException(status_code=404, detail="Run not found")

        await db.execute(
            "UPDATE replay_runs SET status = 'completed', ended_at = ? WHERE id = ? AND repo = ?",
            (now, run_id, resolved_repo),
        )
        await db.commit()
        return {"status": "completed", "ended_at": now}
    finally:
        await db.close()


@router.get("/runs", response_model=list[ReplayRun])
async def list_runs(
    repo: str | None = Query(default=None, description="Repository (owner/repo)"),
) -> list[ReplayRun]:
    """List replay runs for a repo."""
    db = await get_db()
    try:
        resolved_repo = await resolve_repo(repo)
        cursor = await db.execute(
            "SELECT * FROM replay_runs WHERE repo = ? ORDER BY started_at DESC",
            (resolved_repo,),
        )
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
                branch_name=row["branch_name"] if "branch_name" in row.keys() else None,
                total_cost_usd=row["total_cost_usd"] if "total_cost_usd" in row.keys() else 0.0,
            )
            for row in rows
        ]
    finally:
        await db.close()


@router.get("/runs/{run_id}", response_model=ReplayRunWithEvents)
async def get_run(
    run_id: int,
    repo: str | None = Query(default=None, description="Repository (owner/repo)"),
) -> ReplayRunWithEvents:
    """Get a replay run with all its events for playback."""
    db = await get_db()
    try:
        resolved_repo = await resolve_repo(repo)
        cursor = await db.execute("SELECT * FROM replay_runs WHERE id = ?", (run_id,))
        run = await cursor.fetchone()
        if not run or run["repo"] != resolved_repo:
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
                metadata=json.loads(row["metadata"]) if row["metadata"] else {},
                cost_usd=row["cost_usd"] if "cost_usd" in row.keys() else 0.0,
                cumulative_cost_usd=row["cumulative_cost_usd"] if "cumulative_cost_usd" in row.keys() else 0.0,
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
            branch_name=run["branch_name"] if "branch_name" in run.keys() else None,
            total_cost_usd=run["total_cost_usd"] if "total_cost_usd" in run.keys() else 0.0,
            events=events,
            total_duration_ms=total_duration_ms,
        )
    finally:
        await db.close()


@router.post("/demo-seed")
async def seed_demo_data(
    repo: str | None = Query(default=None, description="Repository (owner/repo)"),
) -> dict:
    """Seed a demo replay run with simulated events for presentation purposes.

    Creates a realistic-looking timeline showing Devin fixing alerts much faster
    than Copilot and Anthropic.
    """
    now = datetime.now(timezone.utc).isoformat()
    tools = ["devin", "copilot", "anthropic", "openai", "gemini"]

    db = await get_db()
    try:
        resolved_repo = await resolve_repo(repo)
        # Demo cost totals (sum of all event costs below)
        # Devin: 42 alerts * 0.5 ACU * $2/ACU = $42.00
        # Copilot: 28 alerts * $0.04 = $1.12
        # Anthropic: ~31 calls * ~4500 tok * 2 (in+out) = ~$4.19
        # OpenAI: ~32 calls * ~4500 tok * 2 = ~$2.26
        # Gemini: ~32 calls * ~4500 tok * 2 = ~$2.02
        total_demo_cost = 42.00 + 1.12 + 4.19 + 2.26 + 2.02  # $51.59

        cursor = await db.execute(
            "INSERT INTO replay_runs (repo, scan_id, started_at, ended_at, status, tools, total_cost_usd) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (resolved_repo, None, now, now, "completed", json.dumps(tools), round(total_demo_cost, 4)),
        )
        run_id = cursor.lastrowid
        assert run_id is not None

        # Simulated events with cost tracking
        # Format: (tool, event_type, detail, alert_number, offset_ms, cost_usd)
        demo_events: list[tuple[str, str, str, int | None, int, float]] = [
            # === Scan phase (no cost) ===
            ("devin", "scan_started", "CodeQL scan detected 47 alerts on baseline", None, 0, 0.0),
            ("copilot", "scan_started", "CodeQL scan detected 47 alerts on baseline", None, 0, 0.0),
            ("anthropic", "scan_started", "CodeQL scan detected 47 alerts on baseline", None, 0, 0.0),
            ("openai", "scan_started", "CodeQL scan detected 47 alerts on baseline", None, 0, 0.0),
            ("gemini", "scan_started", "CodeQL scan detected 47 alerts on baseline", None, 0, 0.0),

            # === Devin (fast, fully automated — $2/ACU, ~0.5 ACU per session) ===
            ("devin", "session_created", "Devin session started for CWE-89 SQL Injection", 12, 2000, 1.0),
            ("devin", "analyzing", "Analyzing DataSourceRealm.java:142", 12, 5000, 0.0),
            ("devin", "fix_pushed", "Parameterized query fix committed", 12, 18000, 0.0),
            ("devin", "codeql_verified", "Alert #12 resolved by CodeQL re-scan", 12, 45000, 0.0),

            ("devin", "session_created", "Devin session started for CWE-79 XSS", 15, 8000, 1.0),
            ("devin", "fix_pushed", "Output encoding fix committed", 15, 25000, 0.0),
            ("devin", "codeql_verified", "Alert #15 resolved", 15, 52000, 0.0),

            ("devin", "session_created", "Devin session started for CWE-22 Path Traversal", 18, 12000, 1.0),
            ("devin", "fix_pushed", "Path canonicalization fix committed", 18, 30000, 0.0),
            ("devin", "codeql_verified", "Alert #18 resolved", 18, 58000, 0.0),

            ("devin", "batch_complete", "Batch 1 complete: 8 alerts fixed in 2 minutes", None, 120000, 5.0),
            ("devin", "batch_complete", "Batch 2 complete: 15 alerts fixed in 5 minutes", None, 300000, 12.0),
            ("devin", "batch_complete", "Batch 3 complete: 12 alerts fixed in 8 minutes", None, 480000, 10.0),
            ("devin", "batch_complete", "Batch 4 complete: 7 alerts fixed in 12 minutes", None, 720000, 12.0),
            ("devin", "remediation_complete", "42/47 alerts fixed (89.4%). 5 require manual review.", None, 780000, 0.0),

            # === Copilot Autofix ($0.04 per request) ===
            ("copilot", "suggestion_created", "Autofix suggestion for SQL Injection", 12, 30000, 0.04),
            ("copilot", "waiting_human", "Waiting for developer to review and accept", 12, 30500, 0.0),
            ("copilot", "suggestion_accepted", "Developer accepted fix for alert #12", 12, 600000, 0.0),
            ("copilot", "codeql_verified", "Alert #12 resolved", 12, 900000, 0.0),

            ("copilot", "suggestion_created", "Autofix suggestion for XSS", 15, 35000, 0.04),
            ("copilot", "waiting_human", "Waiting for developer to review", 15, 35500, 0.0),
            ("copilot", "suggestion_accepted", "Developer accepted fix for alert #15", 15, 1200000, 0.0),

            ("copilot", "batch_complete", "8 suggestions accepted after 30 min", None, 1800000, 0.24),
            ("copilot", "batch_complete", "15 suggestions accepted after 1.5 hr", None, 5400000, 0.52),
            ("copilot", "batch_complete", "5 suggestions accepted after 3 hr", None, 10800000, 0.20),
            ("copilot", "remediation_complete",
             "28/47 alerts fixed (59.6%). Required manual acceptance.",
             None, 14400000, 0.08),

            # === Anthropic (claude-opus-4-6: $5 in / $25 out per Mtok) ===
            ("anthropic", "api_call_sent", "Sending alert context to claude-opus-4-6", 12, 5000, 0.0),
            ("anthropic", "patch_generated", "claude-opus-4-6 generated fix for SQL Injection", 12, 15000, 0.135),
            ("anthropic", "patch_applied", "Patch applied to tomcat-anthropic branch", 12, 20000, 0.0),
            ("anthropic", "codeql_verified", "Alert #12 resolved", 12, 65000, 0.0),

            ("anthropic", "api_call_sent", "Sending alert context for XSS to claude-opus-4-6", 15, 18000, 0.0),
            ("anthropic", "patch_generated", "claude-opus-4-6 generated fix for XSS vulnerability", 15, 28000, 0.135),
            ("anthropic", "patch_applied", "Patch applied", 15, 32000, 0.0),
            ("anthropic", "codeql_verified", "Alert #15 resolved", 15, 78000, 0.0),

            ("anthropic", "batch_complete", "Batch 1: 10 fixes applied in 5 min", None, 300000, 1.08),
            ("anthropic", "batch_complete", "Batch 2: 12 fixes applied in 15 min", None, 900000, 1.35),
            ("anthropic", "batch_complete", "Batch 3: 9 fixes applied in 30 min", None, 1800000, 1.215),
            ("anthropic", "remediation_complete",
             "31/47 alerts fixed (66.0%). 3 patches failed CodeQL verification.",
             None, 2700000, 0.135),

            # === OpenAI (gpt-5.3-codex: $1.75 in / $14 out per Mtok) ===
            ("openai", "api_call_sent", "Sending alert context to gpt-5.3-codex", 12, 4000, 0.0),
            ("openai", "patch_generated", "gpt-5.3-codex generated fix for SQL Injection", 12, 12000, 0.0709),
            ("openai", "patch_applied", "Patch applied to tomcat-openai branch", 12, 16000, 0.0),
            ("openai", "codeql_verified", "Alert #12 resolved", 12, 60000, 0.0),

            ("openai", "api_call_sent", "Sending alert context for XSS to gpt-5.3-codex", 15, 14000, 0.0),
            ("openai", "patch_generated", "gpt-5.3-codex generated fix for XSS vulnerability", 15, 24000, 0.0709),
            ("openai", "patch_applied", "Patch applied", 15, 28000, 0.0),
            ("openai", "codeql_verified", "Alert #15 resolved", 15, 72000, 0.0),

            ("openai", "batch_complete", "Batch 1: 11 fixes applied in 4 min", None, 240000, 0.638),
            ("openai", "batch_complete", "Batch 2: 13 fixes applied in 12 min", None, 720000, 0.780),
            ("openai", "batch_complete", "Batch 3: 8 fixes applied in 25 min", None, 1500000, 0.497),
            ("openai", "remediation_complete",
             "32/47 alerts fixed (68.1%). 4 patches failed CodeQL verification.",
             None, 2400000, 0.0709),

            # === Gemini (gemini-3.1-pro-preview: $2 in / $12 out per Mtok) ===
            ("gemini", "api_call_sent", "Sending alert context to gemini-3.1-pro-preview", 12, 3500, 0.0),
            ("gemini", "patch_generated", "gemini-3.1-pro-preview generated fix for SQL Injection", 12, 11000, 0.063),
            ("gemini", "patch_applied", "Patch applied to tomcat-gemini branch", 12, 15000, 0.0),
            ("gemini", "codeql_verified", "Alert #12 resolved", 12, 58000, 0.0),

            ("gemini", "api_call_sent", "Sending alert context for XSS to gemini-3.1-pro-preview", 15, 12000, 0.0),
            ("gemini", "patch_generated", "gemini-3.1-pro-preview generated fix for XSS vulnerability", 15, 22000, 0.063),
            ("gemini", "patch_applied", "Patch applied", 15, 26000, 0.0),
            ("gemini", "codeql_verified", "Alert #15 resolved", 15, 70000, 0.0),

            ("gemini", "batch_complete", "Batch 1: 12 fixes applied in 4 min", None, 240000, 0.630),
            ("gemini", "batch_complete", "Batch 2: 11 fixes applied in 10 min", None, 600000, 0.567),
            ("gemini", "batch_complete", "Batch 3: 9 fixes applied in 22 min", None, 1320000, 0.504),
            ("gemini", "remediation_complete",
             "32/47 alerts fixed (68.1%). 3 patches failed CodeQL verification.",
             None, 2200000, 0.063),
        ]

        # Insert events and track cumulative cost
        cumulative = 0.0
        for tool, event_type, detail, alert_num, offset_ms, cost in demo_events:
            cumulative += cost
            await db.execute(
                """INSERT INTO replay_events
                   (run_id, tool, event_type, detail, alert_number,
                    timestamp_offset_ms, cost_usd, cumulative_cost_usd, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (run_id, tool, event_type, detail, alert_num, offset_ms,
                 round(cost, 6), round(cumulative, 6), now),
            )

        await db.commit()

        return {
            "run_id": run_id,
            "events_created": len(demo_events),
            "total_cost_usd": round(cumulative, 4),
            "message": "Demo replay data seeded successfully",
        }
    finally:
        await db.close()
