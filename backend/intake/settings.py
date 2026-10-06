"""Runtime configuration, read from the environment (and the repo-root .env locally)."""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=REPO_ROOT / ".env", extra="ignore")

    # The API and worker connect as a member of app_role (insert-only audit trail).
    database_url: str = "postgresql://intake_app:app_dev_password@localhost:5433/intakecopilot"
    # Migrations and seeding run as the schema owner.
    database_migration_url: str = (
        "postgresql://intake_owner:owner_dev_password@localhost:5433/intakecopilot"
    )

    data_dir: Path = REPO_ROOT / "data"
    prompts_dir: Path = REPO_ROOT / "prompts"
    migrations_dir: Path = REPO_ROOT / "backend" / "migrations"


@lru_cache
def get_settings() -> Settings:
    return Settings()
