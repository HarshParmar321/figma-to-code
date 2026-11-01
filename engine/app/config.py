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
    # Self-hosted fonts: <font_dir>/<family-slug>/<family-slug>-latin-<weight>-<normal|italic>.woff2
    # (Fontsource naming). Families not found here load from Google Fonts.
    font_dir: Path = ENGINE_DIR / "data" / "fonts"
    # Optional: a specific Chromium/Chrome binary for the fidelity check (default: Playwright's own).
    chromium_path: str | None = None
    # Public base URL used in links returned to the plugin.
    public_url: str = "http://localhost:8000"
    # Figma plugin UIs run in an iframe with origin "null", so CORS must allow it during development.
    cors_origins: list[str] = ["*"]


settings = Settings()
