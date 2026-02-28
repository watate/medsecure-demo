"""Replay recorder — helper for automatically capturing remediation events.

Provides a context-manager-style recorder that creates a replay run,
tracks wall-clock time offsets, and records events with rich metadata
as remediation proceeds.  Also tracks running cost per event.
"""

import json
import logging
import time
from datetime import datetime, timezone

from app.services.database import get_db

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Cost constants
# ---------------------------------------------------------------------------

# Devin: $2.00 per ACU
DEVIN_COST_PER_ACU = 2.0

# Copilot Autofix: $0.04 per request (flat rate)
COPILOT_COST_PER_REQUEST = 0.04

# API tool cost per million tokens (USD)
API_TOOL_PRICING: dict[str, dict[str, float]] = {
    "anthropic": {"input_per_mtok": 5.0, "output_per_mtok": 25.0},
    "openai": {"input_per_mtok": 1.75, "output_per_mtok": 14.0},
    "gemini": {"input_per_mtok": 2.0, "output_per_mtok": 12.0},
}


def compute_llm_call_cost(
    tool: str,
    input_tokens: int | None,
    output_tokens: int | None,
) -> float:
    """Compute cost of a single LLM API call from token counts."""
    pricing = API_TOOL_PRICING.get(tool)
    if not pricing or not input_tokens:
        return 0.0
    inp = (input_tokens / 1_000_000) * pricing["input_per_mtok"]
    out = ((output_tokens or 0) / 1_000_000) * pricing["output_per_mtok"]
    return round(inp + out, 6)


def compute_devin_session_cost(acus: float | None) -> float:
    """Compute cost of a Devin session from ACU count."""
    if not acus or acus <= 0:
        return 0.0
    return round(acus * DEVIN_COST_PER_ACU, 4)


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
        self.repo = repo or ""
        self.run_id: int | None = None
        self._start_time: float = 0.0
        self._cumulative_cost: float = 0.0

    @classmethod
    async def attach(
        cls,
        run_id: int,
        tools: list[str] | None = None,
        repo: str = "",
        start_time: float | None = None,
    ) -> "ReplayRecorder":
        """Attach to an existing replay run (e.g. for benchmark with shared run).

        Unlike ``start()``, this does NOT create a new DB row — it simply
        sets the recorder to write events against the given ``run_id``.

        ``start_time`` should be a ``time.monotonic()`` value captured once
        by the orchestrator so that all concurrent recorders sharing a run
        compute consistent ``timestamp_offset_ms`` values.
        """
        recorder = cls(tools=tools or [], repo=repo)
        recorder.run_id = run_id
        recorder._start_time = start_time if start_time is not None else time.monotonic()
        recorder._cumulative_cost = 0.0
        return recorder

    async def start(self) -> int:
        """Create a replay run and start the clock. Returns the run_id."""
        now = datetime.now(timezone.utc).isoformat()
        self._start_time = time.monotonic()
        self._cumulative_cost = 0.0

        db = await get_db()
        try:
            cursor = await db.execute(
                "INSERT INTO replay_runs"
                " (repo, scan_id, started_at, status, tools, branch_name, total_cost_usd)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (self.repo, self.scan_id, now, "running", json.dumps(self.tools), self.branch_name, 0.0),
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
        cost_usd: float = 0.0,
    ) -> int | None:
        """Record a single event. Returns the event id, or None on failure.

        If ``cost_usd`` is provided it is added to the cumulative total.
        """
        if self.run_id is None:
            logger.warning("ReplayRecorder.record() called before start(), skipping")
            return None

        self._cumulative_cost += cost_usd

        now = datetime.now(timezone.utc).isoformat()
        offset_ms = self._offset_ms()

        # Inject cost fields into metadata for downstream consumers
        meta = dict(metadata or {})
        if cost_usd > 0:
            meta["event_cost_usd"] = round(cost_usd, 6)
        meta["cumulative_cost_usd"] = round(self._cumulative_cost, 6)

        meta_json = json.dumps(meta, default=str)

        db = await get_db()
        try:
            cursor = await db.execute(
                """INSERT INTO replay_events
                   (run_id, tool, event_type, detail, alert_number,
                    timestamp_offset_ms, metadata, cost_usd, cumulative_cost_usd, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    self.run_id, tool, event_type, detail, alert_number,
                    offset_ms, meta_json, round(cost_usd, 6),
                    round(self._cumulative_cost, 6), now,
                ),
            )
            event_id = cursor.lastrowid

            # Update the run's total cost atomically (safe for concurrent recorders)
            await db.execute(
                "UPDATE replay_runs SET total_cost_usd = total_cost_usd + ? WHERE id = ?",
                (round(cost_usd, 6), self.run_id),
            )

            await db.commit()
            logger.debug(
                "Recorded replay event run=%d tool=%s type=%s alert=%s offset=%dms cost=$%.6f cumulative=$%.6f",
                self.run_id, tool, event_type, alert_number, offset_ms, cost_usd, self._cumulative_cost,
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
            logger.info(
                "Finished replay recording run_id=%d status=%s total_cost=$%.4f",
                self.run_id, status, self._cumulative_cost,
            )
        finally:
            await db.close()
