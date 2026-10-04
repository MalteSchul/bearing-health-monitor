from pathlib import Path

from pydantic import AliasChoices, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    """Runtime configuration, overridable via environment variables prefixed with MONITOR_."""

    # .env holds local secrets such as the API key and is never committed; real environment
    # variables win over it. Other tools' entries in it are ignored.
    model_config = SettingsConfigDict(
        env_prefix="MONITOR_", env_file=PROJECT_ROOT / ".env", extra="ignore"
    )

    # Directory served at "/". Plain HTML today; point it at frontend/dist for a Vite build.
    frontend_dir: Path = PROJECT_ROOT / "frontend"
    # Feature table from scripts/extract_features.py, loaded once at startup.
    features_path: Path = PROJECT_ROOT / "data" / "processed" / "features.parquet"
    # Origins allowed to call the API directly, e.g. a Vite dev server on http://localhost:5173.
    cors_origins: list[str] = []
    # Git commit the running build was made from. CI bakes it into the image; "dev" otherwise.
    commit_sha: str = "dev"
    # The usual variable name, without the prefix. Without a key the copilot still returns the
    # facts it looked up, only no written answer.
    anthropic_api_key: SecretStr | None = Field(
        default=None, validation_alias=AliasChoices("ANTHROPIC_API_KEY", "anthropic_api_key")
    )
    copilot_model: str = "claude-opus-5-5"
    # Every question costs money and the URL is public.
    copilot_questions_per_minute: int = 10
