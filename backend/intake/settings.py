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

    # "openai" for real model calls; "dev-oracle" answers from gold labels for UI development
    # and tests without a key. Dev-oracle outputs are labelled as such and never count as
    # model metrics.
    llm_backend: str = "openai"
    openai_api_key: str = ""
    openai_extract_model: str = "gpt-5.5"
    openai_triage_model: str = "gpt-5.5"
    openai_protocol_model: str = "gpt-5.4-mini"
    openai_embedding_model: str = "text-embedding-3-small"
    openai_timeout_seconds: float = 90.0
    # Reasoning effort for the structured-output calls ("none", "low", "medium", ...).
    openai_reasoning_effort: str = "low"
    # USD per million tokens: (input, cached input, output). ESTIMATES for the dashboard's cost
    # column; set PRICE_TABLE to current published rates before quoting costs.
    price_table: dict[str, tuple[float, float, float]] = {
        "gpt-5.5": (1.25, 0.125, 10.0),
        "gpt-5.4-mini": (0.25, 0.025, 2.0),
        "text-embedding-3-small": (0.02, 0.02, 0.0),
    }

    # Prompt versions used by the live pipeline (the eval runner can override per run).
    prompt_versions: dict[str, str] = {"extract": "v1", "triage": "v1", "protocol": "v1"}

    run_worker: bool = True
    # Public demo only: reset live cases to the demo queue once a day at this UTC hour.
    demo_reset_utc_hour: int | None = None
    worker_concurrency: int = 3
    session_secret: str = "dev-only-session-secret-change-me-0123456789"
    session_hours: int = 8
    secure_cookies: bool = False
    seed_staff_password: str = "lakeshore-demo"
    # Public demo protections.
    max_uploads_per_user_per_day: int = 40
    max_upload_bytes: int = 10 * 1024 * 1024
    daily_spend_cap_usd: float = 5.0

    storage_dir: Path = REPO_ROOT / "storage"
    data_dir: Path = REPO_ROOT / "data"
    prompts_dir: Path = REPO_ROOT / "prompts"
    migrations_dir: Path = REPO_ROOT / "backend" / "migrations"


@lru_cache
def get_settings() -> Settings:
    return Settings()
