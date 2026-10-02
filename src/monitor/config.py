from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    """Runtime configuration, overridable via environment variables prefixed with MONITOR_."""

    model_config = SettingsConfigDict(env_prefix="MONITOR_")

    # Directory served at "/". Plain HTML today; point it at frontend/dist for a Vite build.
    frontend_dir: Path = PROJECT_ROOT / "frontend"
    # Origins allowed to call the API directly, e.g. a Vite dev server on http://localhost:5173.
    cors_origins: list[str] = []
    # Git commit the running build was made from. CI bakes it into the image; "dev" otherwise.
    commit_sha: str = "dev"
