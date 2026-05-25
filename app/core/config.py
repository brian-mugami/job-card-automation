from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    local_database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/job_card_db"
    app_secret_key: str = "change-this-local-secret-before-sharing"
    token_ttl_minutes: int = 720
    password_reset_ttl_minutes: int = 30
    frontend_url: str = "http://localhost:8501"

    # CORS — comma-separated list of allowed origins. Default permissive for
    # local dev; tighten in production by listing only your real domains.
    cors_allow_origins: str = "*"

    # Swagger UI / OpenAPI exposure. Defaults to **off** so a public deploy
    # doesn't hand out a free attack-surface map. Flip to ``true`` only on
    # local dev or behind admin-only auth.
    expose_api_docs: bool = False

    # One-time setup secret used to bootstrap the first admin from a non-
    # loopback origin (e.g. through the public Easypanel domain). When set,
    # any login on an empty users table that supplies a matching
    # ``X-Bootstrap-Secret`` header is allowed through. Leave blank in normal
    # operation — once the admin exists the value is ignored anyway.
    bootstrap_secret: str = ""

    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_from_email: str = ""
    smtp_from_name: str = "Job Card Automation System"
    smtp_use_tls: bool = True
    smtp_enabled: bool = False
    smtp_timeout_seconds: int = 8


    pdf_company_name: str = "Your Garage Name"
    pdf_company_address: str = "Your garage address"
    pdf_company_phone: str = "Your phone number"
    pdf_company_email: str = "your-email@example.com"
    pdf_currency: str = "KES"

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    return Settings()
