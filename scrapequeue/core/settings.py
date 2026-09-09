"""Process-wide configuration, read once from environment variables.

Every service (api, workers, beat, migrate) imports the same object so that a
value changed in .env changes everywhere. Nothing here reads files at runtime
other than the schedule/queue YAML, which is loaded by the modules that own it.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

Scope = Literal["read", "write", "admin"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- identity / networking ---
    app_name: str = "scrape-queue"
    environment: Literal["dev", "test", "prod"] = "dev"
    log_level: str = "INFO"
    user_agent: str = Field(default="scrape-queue", alias="SCRAPEQUEUE_USER_AGENT")

    # --- auth: "key:scope,key:scope" ---
    api_keys: SecretStr = Field(default=SecretStr(""), alias="API_KEYS")
    api_rate_limit_per_minute: int = 60

    # --- data stores ---
    database_url: str = Field(alias="DATABASE_URL")
    redis_url: str = Field(alias="REDIS_URL")
    redbeat_redis_url: str = Field(alias="REDBEAT_REDIS_URL")

    # --- object storage ---
    s3_endpoint: str = Field(alias="S3_ENDPOINT")
    s3_bucket: str = Field(alias="S3_BUCKET")
    s3_access_key: SecretStr = Field(alias="S3_ACCESS_KEY")
    s3_secret_key: SecretStr = Field(alias="S3_SECRET_KEY")
    s3_presign_ttl_seconds: int = 900

    # --- queue behaviour ---
    # Hard ceiling for one crawl task. visibility_timeout must exceed this,
    # otherwise Redis re-delivers a task that is still legitimately running.
    task_time_limit_seconds: int = 1800
    task_soft_time_limit_seconds: int = 1500
    visibility_timeout_seconds: int = 3600
    max_job_retries: int = 5
    retry_backoff_base_seconds: int = 30
    retry_backoff_max_seconds: int = 1800
    # A task that has been lost to worker death this many times is a poison pill.
    poison_pill_threshold: int = 3
    # Backpressure: POST /jobs returns 429 above this many queued messages.
    queue_depth_limit: int = 500

    # --- scraping guards ---
    allowed_target_hosts: list[str] = Field(
        default_factory=lambda: [
            "simpler.grants.gov",
            "api.grants.gov",
            "www.federalregister.gov",
        ]
    )
    default_domain_rate_per_sec: float = 1.0
    # Only for environments behind a TLS-intercepting proxy. Off by default:
    # silently accepting any certificate is not something a scraper should do
    # unless an operator has decided it must.
    browser_ignore_https_errors: bool = False
    circuit_failure_threshold: int = 5
    circuit_open_seconds: int = 300
    # Drift: a page yielding fewer rows than baseline * ratio marks the job SUSPECT.
    drift_min_ratio: float = 0.5

    # --- observability ---
    otel_exporter_otlp_endpoint: str | None = Field(default=None, alias="OTEL_EXPORTER_OTLP_ENDPOINT")
    metrics_port: int = 9464

    def api_key_scopes(self) -> dict[str, Scope]:
        raw = self.api_keys.get_secret_value()
        out: dict[str, Scope] = {}
        for pair in filter(None, (p.strip() for p in raw.split(","))):
            key, _, scope = pair.partition(":")
            if scope not in ("read", "write", "admin"):
                raise ValueError(f"API_KEYS: bad scope for key ending {key[-4:]!r}")
            out[key] = scope  # type: ignore[assignment]
        return out


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
