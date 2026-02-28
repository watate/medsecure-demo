from fastapi import APIRouter, Query

from app.models.schemas import RepoConfig
from app.services.repo_resolver import resolve_baseline_branch, resolve_repo

router = APIRouter(prefix="/api/config", tags=["config"])


@router.get("", response_model=RepoConfig)
async def get_config(
    repo: str | None = Query(default=None, description="Repository (owner/repo)"),
) -> RepoConfig:
    """Get current repository configuration for a repo."""
    resolved_repo = await resolve_repo(repo)
    baseline_branch = await resolve_baseline_branch(resolved_repo)
    return RepoConfig(
        github_repo=resolved_repo,
        branch_baseline=baseline_branch,
    )
