from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # GitHub
    github_token: str = ""
    # No default repo fallback — UI must select a tracked repo
    github_repo: str = ""

    # Default baseline branch
    branch_baseline: str = "main"

    # Devin API
    devin_api_key: str = ""
    devin_api_base: str = "https://api.devin.ai/v1"

    # Anthropic API
    anthropic_api_key: str = ""

    # OpenAI API
    openai_api_key: str = ""

    # Google Gemini API
    gemini_api_key: str = ""

    # Remediation batching (applies to all tools)
    batch_size: int = 10

    # Database
    database_path: str = "medsecure.db"

    # S3 backup
    s3_backup_bucket: str = ""
    aws_region: str = "ap-southeast-1"

    # Auth — path to better-auth SQLite database
    auth_db_path: str = "../frontend/auth.db"

    # CORS
    cors_origins: str = "http://localhost:3000"

    model_config = {"env_file": ".env", "extra": "ignore"}


settings = Settings()
