"""Shared utilities to resolve repo + tool branches."""

import json

from app.config import settings
from app.services.database import get_db


async def resolve_repo(repo: str | None) -> str:
    """Resolve repo: explicit param > first tracked repo > settings fallback."""
    if repo:
        return repo
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT full_name FROM repos ORDER BY added_at ASC LIMIT 1"
        )
        row = await cursor.fetchone()
        if row:
            return row["full_name"]
    finally:
        await db.close()
    return settings.github_repo


async def resolve_baseline_branch(repo: str) -> str:
    """Resolve the baseline branch for a repo.

    Uses the tracked repo's default_branch if present; otherwise falls back to
    BRANCH_BASELINE from config.
    """
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT default_branch FROM repos WHERE full_name = ? LIMIT 1",
            (repo,),
        )
        row = await cursor.fetchone()
        if row and row["default_branch"]:
            return row["default_branch"]
    finally:
        await db.close()
    return settings.branch_baseline


async def get_latest_tool_branches(repo: str) -> dict[str, str]:

    """Return the latest known branch_name per tool for the given repo.

    We infer tool branches from replay_runs (each remediation run stores the
    branch it created). This keeps scan/report flows working without static
    branch env vars.
    """
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT tools, branch_name FROM replay_runs "
            "WHERE repo = ? AND branch_name IS NOT NULL "
            "ORDER BY started_at DESC",
            (repo,),
        )
        rows = await cursor.fetchall()

        branches: dict[str, str] = {}
        for row in rows:
            branch_name = row["branch_name"]
            if not branch_name:
                continue
            try:
                tools = json.loads(row["tools"] or "[]")
            except Exception:
                tools = []

            for tool in tools:
                if tool == "baseline":
                    continue
                if tool not in branches:
                    branches[tool] = branch_name

        return branches
    finally:
        await db.close()


async def resolve_branch(repo: str, tool: str, branch: str | None = None) -> str:
    """Resolve branch for a tool.

    Priority: explicit branch > baseline mapping > latest replay branch for tool > baseline.
    """
    if branch:
        return branch
    if tool == "baseline":
        return await resolve_baseline_branch(repo)

    branches = await get_latest_tool_branches(repo)
    return branches.get(tool, settings.branch_baseline)
