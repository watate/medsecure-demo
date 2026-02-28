"""Repository management endpoints — list, add, remove tracked repos."""

import logging

from fastapi import APIRouter, HTTPException, Query

from app.models.schemas import GitHubRepoInfo, Repo, RepoAdd
from app.services.database import get_db
from app.services.github_client import GitHubClient

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/repos", tags=["repos"])


@router.get("/available", response_model=list[GitHubRepoInfo])
async def list_available_repos(
    search: str | None = Query(default=None, description="Filter repos by name"),
) -> list[GitHubRepoInfo]:
    """List repositories accessible by the configured GitHub PAT."""
    github = GitHubClient()
    raw_repos = await github.list_accessible_repos()

    results = [GitHubRepoInfo(**r) for r in raw_repos]

    if search:
        q = search.lower()
        results = [r for r in results if q in r.full_name.lower()]

    return results


@router.get("", response_model=list[Repo])
async def list_repos() -> list[Repo]:
    """List all tracked (added) repositories."""
    db = await get_db()
    try:
        cursor = await db.execute("SELECT * FROM repos ORDER BY added_at DESC")
        rows = await cursor.fetchall()
        return [
            Repo(
                id=row["id"],
                full_name=row["full_name"],
                default_branch=row["default_branch"],
                added_at=row["added_at"],
            )
            for row in rows
        ]
    finally:
        await db.close()


@router.post("", response_model=Repo)
async def add_repo(request: RepoAdd) -> Repo:
    """Add a repository to track.

    Validates the repo exists and is accessible via the PAT, then stores it.
    """
    github = GitHubClient()

    # Validate the repo exists and is accessible
    try:
        info = await github.get_repo_info(request.full_name)
    except Exception as e:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot access repo '{request.full_name}': {e}",
        ) from e

    db = await get_db()
    try:
        # Check for duplicates
        cursor = await db.execute(
            "SELECT id FROM repos WHERE full_name = ?",
            (info["full_name"],),
        )
        existing = await cursor.fetchone()
        if existing:
            raise HTTPException(
                status_code=409,
                detail=f"Repo '{info['full_name']}' is already tracked",
            )

        cursor = await db.execute(
            "INSERT INTO repos (full_name, default_branch) VALUES (?, ?)",
            (info["full_name"], info["default_branch"]),
        )
        repo_id = cursor.lastrowid
        assert repo_id is not None
        await db.commit()

        cursor = await db.execute("SELECT * FROM repos WHERE id = ?", (repo_id,))
        row = await cursor.fetchone()
        assert row is not None

        return Repo(
            id=row["id"],
            full_name=row["full_name"],
            default_branch=row["default_branch"],
            added_at=row["added_at"],
        )
    finally:
        await db.close()


@router.delete("/{repo_id}")
async def remove_repo(repo_id: int) -> dict:
    """Remove a tracked repository."""
    db = await get_db()
    try:
        cursor = await db.execute("SELECT * FROM repos WHERE id = ?", (repo_id,))
        row = await cursor.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Repo not found")

        await db.execute("DELETE FROM repos WHERE id = ?", (repo_id,))
        await db.commit()
        return {"deleted": row["full_name"]}
    finally:
        await db.close()
