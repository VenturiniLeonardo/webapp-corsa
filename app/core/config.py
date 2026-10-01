from functools import lru_cache
from typing import Annotated, Literal

from pydantic import field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    STRAVA_CLIENT_ID: str
    STRAVA_CLIENT_SECRET: str
    STRAVA_API_BASE: str = "https://www.strava.com/api/v3"
    HEALTHCHECK_URL_SYNC: str = ""
    ALLOWED_LOGINS: Annotated[set[str], NoDecode]
    DATABASE_URL: str
    ENV: Literal["dev", "prod"]
    AUTH_DEV_LOGIN: str

    # AI (OpenRouter). Empty key = feature disabled. See docs/PLAN.md §26.
    OPENROUTER_API_KEY: str = ""
    OPENROUTER_BASE_URL: str = "https://openrouter.ai/api/v1"
    # Fixed fallback order: primary -> fallback 1 -> fallback 2. Never reordered at runtime.
    AI_PRIMARY_MODEL: str = "nvidia/nemotron-3-ultra-550b-a55b:free"
    AI_FALLBACK_MODEL_1: str = "nvidia/nemotron-3-super-120b-a12b:free"
    AI_FALLBACK_MODEL_2: str = "qwen/qwen3.8-27b:free"
    AI_TIMEOUT_S: float = 60.0  # per HTTP attempt
    AI_MAX_TOKENS: int = 4000  # reasoning models spend part of it before answering
    AI_TEMPERATURE: float = 0.2
    AI_MAX_RETRIES: int = 1  # same-model retries on 429/5xx, then next model
    AI_RETRY_BACKOFF_S: float = 2.0  # doubled per retry; Retry-After above AI_MAX_WAIT_S skips
    AI_MAX_WAIT_S: float = 10.0
    AI_DAILY_LIMIT: int = 40  # OpenRouter free: 50/day; margin for manual use
    AI_MINUTE_LIMIT: int = 10  # OpenRouter free: 20/min
    # Runner profile sent with every AI request (single user, ADR-15). Age/BMI derived, never stored.
    RUNNER_BIRTH_YEAR: int | None = None
    RUNNER_HEIGHT_CM: int | None = None
    RUNNER_WEIGHT_KG: float | None = None
    RUNNER_SERIOUS_SINCE: str = "2026-01-01"  # earlier runs in the data were occasional
    RUNNER_RUNS_PER_WEEK: int = 3
    RUNNER_GYM_PER_WEEK: int = 3

    @property
    def ai_models(self) -> list[str]:
        return [
            m
            for m in (self.AI_PRIMARY_MODEL, self.AI_FALLBACK_MODEL_1, self.AI_FALLBACK_MODEL_2)
            if m
        ]

    @field_validator("ALLOWED_LOGINS", mode="before")
    @classmethod
    def _split(cls, v: object) -> object:
        if isinstance(v, str):
            return {s.strip() for s in v.split(",") if s.strip()}
        return v


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
