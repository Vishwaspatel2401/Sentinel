# =============================================================================
# FILE: backend/config.py
# WHAT: Single source of truth for all configuration and secrets.
#       Reads values from the .env file at project root.
# WHY:  Instead of calling os.environ.get("KEY") scattered across 20 files,
#       every file imports `settings` from here and uses settings.anthropic_api_key.
#       If a required variable is missing, the app crashes at startup with a
#       clear error — not silently at 3 AM during an incident.
# CONNECTED TO:
#   → backend/db/database.py uses settings.database_url to connect to Postgres
#   → backend/services/llm_service.py uses settings.anthropic_api_key + settings.llm_model
#   → backend/alembic/env.py uses settings.database_url for migrations
# =============================================================================

from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict

# Resolves to project root .env regardless of where the command is run from
ENV_FILE = str(Path(__file__).resolve().parent.parent / ".env")

# Default data dir: two levels up from config.py (Sentinel/backend/ → Sentinel/) + data/
# This is correct for local development where data/ lives at the project root.
# In Docker, DATA_DIR is overridden to /app/data via docker-compose environment.
_DEFAULT_DATA_DIR = str(Path(__file__).resolve().parent.parent / "data")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ENV_FILE, env_file_encoding="utf-8")

    # LLM
    anthropic_api_key: str
    llm_model: str = "claude-haiku-4-5-20251001"

    # Database
    database_url: str
    redis_url: str

    # API
    api_secret_key: str = "sentinel-dev-key"

    # App
    debug: bool = False
    log_level: str = "INFO"

    # Data directory — where RAG index files (runbooks.index, chunks.json, bm25.pkl) live.
    # Overridden to /app/data in docker-compose so the container finds the mounted volume.
    data_dir: str = _DEFAULT_DATA_DIR


settings = Settings()

