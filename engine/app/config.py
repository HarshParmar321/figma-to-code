from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

ENGINE_DIR = Path(__file__).resolve().parent.parent
REPO_DIR = ENGINE_DIR.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="ENGINE_", env_file=".env", extra="ignore")

    data_dir: Path = ENGINE_DIR / "data"
    schema_path: Path = REPO_DIR / "schemas" / "bundle.schema.json"
    max_bundle_bytes: int = 50 * 1024 * 1024  # bundle JSON only; assets upload separately
    max_asset_bytes: int = 40 * 1024 * 1024
    # Figma plugin UIs run in an iframe with origin "null", so CORS must allow it during development.
    cors_origins: list[str] = ["*"]


settings = Settings()
