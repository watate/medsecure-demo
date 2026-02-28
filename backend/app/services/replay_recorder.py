"""Replay recorder — helper for automatically capturing remediation events.

Provides a context-manager-style recorder that creates a replay run,
tracks wall-clock time offsets, and records events with rich metadata
as remediation proceeds.
"""

import json
import logging
import time
from datetime import datetime, timezone

from app.config import settings
from app.services.database import get_db

logger = logging.getLogger(__name__)


class ReplayRecorder:
    """Records remediation events into the replay system.

    Usage::

        recorder = ReplayRecorder(tools=["anthropic", "openai"])
        await recorder.start()

        await recorder.record(
            tool="anthropic",
            event_type="api_call_sent",
            detail="Sending alert context to claude-opus-4-6",
            alert_number=12,
            metadata={"model": "claude-opus-4-6", "file_path": "Foo.java"},
        )

        await recorder.finish()
    """

    def __init__(
        self,
        tools: list[str],
        scan_id: int | None = None,
        branch_name: str | None = None,
        repo: str | None = None,
    ) -> None:
        self.tools = tools
        self.scan_id = scan_id
        self.branch_name = branch_name
        self.repo = repo or settings.github_repo
        self.run_id: int | None = None
        self._start_time: float = 0.0

    async def start(self) -> int:
        """Create a replay run and start the clock. Returns the run_id."""
        now = datetime.now(timezone.utc).isoformat()
        self._start_time = time.monotonic()

        db = await get_db()
        try:
            cursor = await db.execute(
                "INSERT INTO replay_runs"
                " (repo, scan_id, started_at, status, tools, branch_name)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (self.repo, self.scan_id, now, "running", json.dumps(self.tools), self.branch_name),
            )
            self.run_id = cursor.lastrowid
            assert self.run_id is not None
            await db.commit()
            logger.info("Started replay recording run_id=%d tools=%s", self.run_id, self.tools)
            return self.run_id
        finally:
            await db.close()

    def _offset_ms(self) -> int:
        """Milliseconds elapsed since start()."""
        if self._start_time == 0.0:
            return 0
        return int((time.monotonic() - self._start_time) * 1000)

    async def record(
        self,
        tool: str,
        event_type: str,
        detail: str,
        alert_number: int | None = None,
        metadata: dict[str, object] | None = None,
    ) -> int | None:
        """Record a single event. Returns the event id, or None on failure."""
        if self.run_id is None:
            logger.warning("ReplayRecorder.record() called before start(), skipping")
            return None

        now = datetime.now(timezone.utc).isoformat()
        offset_ms = self._offset_ms()
        meta_json = json.dumps(metadata or {}, default=str)

        db = await get_db()
        try:
            cursor = await db.execute(
                """INSERT INTO replay_events
                   (run_id, tool, event_type, detail, alert_number, timestamp_offset_ms, metadata, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (self.run_id, tool, event_type, detail, alert_number, offset_ms, meta_json, now),
            )
            event_id = cursor.lastrowid
            await db.commit()
            logger.debug(
                "Recorded replay event run=%d tool=%s type=%s alert=%s offset=%dms",
                self.run_id, tool, event_type, alert_number, offset_ms,
            )
            return event_id
        except Exception:
            logger.exception("Failed to record replay event")
            return None
        finally:
            await db.close()

    async def finish(self, status: str = "completed") -> None:
        """Mark the replay run as finished."""
        if self.run_id is None:
            return

        now = datetime.now(timezone.utc).isoformat()
        db = await get_db()
        try:
            await db.execute(
                "UPDATE replay_runs SET status = ?, ended_at = ? WHERE id = ?",
                (status, now, self.run_id),
            )
            await db.commit()
            logger.info("Finished replay recording run_id=%d status=%s", self.run_id, status)
        finally:
            await db.close()
