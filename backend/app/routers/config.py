from fastapi import APIRouter

from app.config import settings
from app.models.schemas import RepoConfig

router = APIRouter(prefix="/api/config", tags=["config"])


@router.get("", response_model=RepoConfig)
async def get_config() -> RepoConfig:
    """Get current repository configuration."""
    return RepoConfig(
        github_repo=settings.github_repo,
        branch_baseline=settings.branch_baseline,
        branch_devin=settings.branch_devin,
        branch_copilot=settings.branch_copilot,
        branch_anthropic=settings.branch_anthropic,
        branch_openai=settings.branch_openai,
        branch_google=settings.branch_google,
    )
