from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    REAL,
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utcnow_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class Base(DeclarativeBase):
    pass


def _in(col: str, values: tuple[str, ...], name: str) -> CheckConstraint:
    return CheckConstraint(f"{col} IN ({', '.join(repr(v) for v in values)})", name=name)


class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (
        _in("status", ("queued", "running", "done", "failed"), "ck_jobs_status"),
        Index("ix_jobs_status_not_before", "status", "not_before"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, server_default="queued")
    params: Mapped[Any] = mapped_column(JSON, nullable=True)
    progress_done: Mapped[int | None] = mapped_column(Integer)
    progress_total: Mapped[int | None] = mapped_column(Integer)
    attempts: Mapped[int] = mapped_column(Integer, server_default=text("0"), default=0)
    error: Mapped[str | None] = mapped_column(Text)
    not_before: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(Text, default=utcnow_iso)
    started_at: Mapped[str | None] = mapped_column(Text)
    heartbeat_at: Mapped[str | None] = mapped_column(Text)
    finished_at: Mapped[str | None] = mapped_column(Text)


class Activity(Base):
    __tablename__ = "activities"
    __table_args__ = (
        _in("sport_type", ("run", "trail_run", "treadmill"), "ck_activities_sport_type"),
        _in(
            "workout_type",
            ("easy", "long", "workout", "race", "other"),
            "ck_activities_workout_type",
        ),
        CheckConstraint("distance_m >= 0", name="ck_activities_distance_m"),
        CheckConstraint(
            "difficulty IS NULL OR difficulty BETWEEN 1 AND 10", name="ck_activities_difficulty"
        ),
        Index("ix_activities_local_date", "local_date"),
        Index("ix_activities_start_time_utc", "start_time_utc"),
        Index("ix_activities_sport_type_local_date", "sport_type", "local_date"),
        Index("ix_activities_distance_m", "distance_m"),
        Index("ix_activities_workout_type", "workout_type"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    sport_type: Mapped[str] = mapped_column(Text)
    name: Mapped[str | None] = mapped_column(Text)
    start_time_utc: Mapped[str] = mapped_column(Text)
    timezone: Mapped[str | None] = mapped_column(Text)
    local_date: Mapped[str] = mapped_column(Text)
    elapsed_s: Mapped[int | None] = mapped_column(Integer)
    moving_s: Mapped[int | None] = mapped_column(Integer)
    distance_m: Mapped[float | None] = mapped_column(REAL)
    elev_gain_m: Mapped[float | None] = mapped_column(REAL)
    elev_loss_m: Mapped[float | None] = mapped_column(REAL)
    avg_hr: Mapped[float | None] = mapped_column(REAL)
    max_hr: Mapped[float | None] = mapped_column(REAL)
    avg_cadence_spm: Mapped[float | None] = mapped_column(REAL)
    avg_power_w: Mapped[float | None] = mapped_column(REAL)
    calories_kcal: Mapped[int | None] = mapped_column(Integer)
    has_gps: Mapped[bool | None] = mapped_column(Boolean)
    has_hr: Mapped[bool | None] = mapped_column(Boolean)
    has_cadence: Mapped[bool | None] = mapped_column(Boolean)
    is_indoor: Mapped[bool | None] = mapped_column(Boolean)
    workout_type: Mapped[str | None] = mapped_column(Text)
    notes: Mapped[str | None] = mapped_column(Text)
    difficulty: Mapped[int | None] = mapped_column(Integer)  # perceived effort 1-10 (user)
    excluded_from_stats: Mapped[bool] = mapped_column(
        Boolean, server_default=text("0"), default=False
    )
    # use_alter: activities <-> source_records is an FK cycle
    primary_source_id: Mapped[int | None] = mapped_column(
        ForeignKey("source_records.id", use_alter=True, name="fk_activities_primary_source")
    )
    stream_source_id: Mapped[int | None] = mapped_column(
        ForeignKey("source_records.id", use_alter=True, name="fk_activities_stream_source")
    )
    summary_polyline: Mapped[str | None] = mapped_column(Text)
    # Open-Meteo at the run midpoint (opt-in, setting weather_enabled); not from the source
    weather_temp_c: Mapped[float | None] = mapped_column(REAL)
    weather_dew_point_c: Mapped[float | None] = mapped_column(REAL)
    duplicate_of_id: Mapped[int | None] = mapped_column(ForeignKey("activities.id"))
    upstream_deleted_at: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(Text, default=utcnow_iso)
    updated_at: Mapped[str] = mapped_column(Text, default=utcnow_iso, onupdate=utcnow_iso)


class SourceRecord(Base):
    __tablename__ = "source_records"
    __table_args__ = (
        UniqueConstraint("source", "external_id", name="uq_source_records_source_external_id"),
        _in(
            "source",
            ("strava", "file_fit", "file_gpx", "file_tcx", "apple_health"),
            "ck_source_records_source",
        ),
        _in(
            "status",
            ("pending", "mapped", "duplicate", "skipped", "error"),
            "ck_source_records_status",
        ),
        Index("ix_source_records_activity_id", "activity_id"),
        Index("ix_source_records_status", "status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    source: Mapped[str] = mapped_column(Text)
    external_id: Mapped[str] = mapped_column(Text)
    activity_id: Mapped[int | None] = mapped_column(ForeignKey("activities.id"))
    status: Mapped[str] = mapped_column(Text, server_default="pending")
    error: Mapped[str | None] = mapped_column(Text)
    raw_summary: Mapped[Any] = mapped_column(JSON, nullable=True)
    raw_detail: Mapped[Any] = mapped_column(JSON, nullable=True)
    file_path: Mapped[str | None] = mapped_column(Text)
    source_start_time_utc: Mapped[str | None] = mapped_column(Text)
    fetched_at: Mapped[str | None] = mapped_column(Text)
    mapper_version: Mapped[int | None] = mapped_column(Integer)
    job_id: Mapped[int | None] = mapped_column(ForeignKey("jobs.id"))


class Stream(Base):
    __tablename__ = "streams"

    source_record_id: Mapped[int] = mapped_column(
        ForeignKey("source_records.id", ondelete="CASCADE"), primary_key=True
    )
    activity_id: Mapped[int] = mapped_column(
        ForeignKey("activities.id", ondelete="CASCADE"), index=True
    )
    n_points: Mapped[int] = mapped_column(Integer)
    channels: Mapped[str] = mapped_column(Text)
    data: Mapped[bytes] = mapped_column(LargeBinary)
    codec_version: Mapped[int] = mapped_column(Integer)


class Lap(Base):
    __tablename__ = "laps"
    __table_args__ = (
        UniqueConstraint("activity_id", "kind", "idx", name="uq_laps_activity_kind_idx"),
        _in("kind", ("device_lap", "split_km"), "ck_laps_kind"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    activity_id: Mapped[int] = mapped_column(ForeignKey("activities.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(Text)
    idx: Mapped[int] = mapped_column(Integer)
    start_offset_s: Mapped[int | None] = mapped_column(Integer)
    elapsed_s: Mapped[int | None] = mapped_column(Integer)
    moving_s: Mapped[int | None] = mapped_column(Integer)
    distance_m: Mapped[float | None] = mapped_column(REAL)
    avg_speed_ms: Mapped[float | None] = mapped_column(REAL)
    avg_hr: Mapped[float | None] = mapped_column(REAL)
    max_hr: Mapped[float | None] = mapped_column(REAL)
    avg_cadence_spm: Mapped[float | None] = mapped_column(REAL)
    elev_gain_m: Mapped[float | None] = mapped_column(REAL)
    gap_speed_ms: Mapped[float | None] = mapped_column(REAL)  # grade-adjusted (model)


class BestEffort(Base):
    __tablename__ = "best_efforts"
    __table_args__ = (
        UniqueConstraint("activity_id", "distance_m", name="uq_best_efforts_activity_distance"),
        Index("ix_best_efforts_distance_m_elapsed_s", "distance_m", "elapsed_s"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    activity_id: Mapped[int] = mapped_column(ForeignKey("activities.id", ondelete="CASCADE"))
    distance_m: Mapped[float] = mapped_column(REAL)
    elapsed_s: Mapped[int] = mapped_column(Integer)
    start_offset_s: Mapped[int | None] = mapped_column(Integer)
    algo_version: Mapped[int] = mapped_column(Integer)


class ActivityMetrics(Base):
    __tablename__ = "activity_metrics"

    activity_id: Mapped[int] = mapped_column(
        ForeignKey("activities.id", ondelete="CASCADE"), primary_key=True
    )
    algo_version: Mapped[int] = mapped_column(Integer)
    computed_at: Mapped[str] = mapped_column(Text, default=utcnow_iso)
    zones_hash: Mapped[str | None] = mapped_column(Text)
    time_in_zones_s: Mapped[Any] = mapped_column(JSON, nullable=True)
    efficiency_factor: Mapped[float | None] = mapped_column(REAL)
    pace_cv: Mapped[float | None] = mapped_column(REAL)
    is_steady: Mapped[bool | None] = mapped_column(Boolean)
    decoupling_pct: Mapped[float | None] = mapped_column(REAL)
    trimp: Mapped[float | None] = mapped_column(REAL)
    gps_suspect: Mapped[bool | None] = mapped_column(Boolean)
    gap_speed_ms: Mapped[float | None] = mapped_column(REAL)  # grade-adjusted (model)
    hr_at_ref_pace: Mapped[float | None] = mapped_column(REAL)
    ef_adjusted: Mapped[float | None] = mapped_column(REAL)  # GAP + heat (model)


class Tag(Base):
    __tablename__ = "tags"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(collation="NOCASE"), unique=True)


class ActivityTag(Base):
    __tablename__ = "activity_tags"

    activity_id: Mapped[int] = mapped_column(
        ForeignKey("activities.id", ondelete="CASCADE"), primary_key=True
    )
    tag_id: Mapped[int] = mapped_column(ForeignKey("tags.id", ondelete="CASCADE"), primary_key=True)


class ProviderAccount(Base):
    __tablename__ = "provider_accounts"

    provider: Mapped[str] = mapped_column(Text, primary_key=True)
    athlete_id: Mapped[str | None] = mapped_column(Text)
    access_token: Mapped[str | None] = mapped_column(Text)
    refresh_token: Mapped[str | None] = mapped_column(Text)
    expires_at: Mapped[int | None] = mapped_column(Integer)
    scopes: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str | None] = mapped_column(Text)
    sync_cursor: Mapped[int | None] = mapped_column(Integer)
    last_sync_at: Mapped[str | None] = mapped_column(Text)
    last_reconcile_at: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[str] = mapped_column(Text, default=utcnow_iso, onupdate=utcnow_iso)


class Setting(Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(Text, primary_key=True)
    value: Mapped[Any] = mapped_column(JSON, nullable=True)


class AiAnalysis(Base):
    """Validated LLM output cache. Key = kind + subject + prompt version + hash of the input."""

    __tablename__ = "ai_analyses"
    __table_args__ = (
        UniqueConstraint("kind", "input_hash", name="uq_ai_analyses_kind_input_hash"),
        _in("kind", ("activity", "period"), "ck_ai_analyses_kind"),
        Index("ix_ai_analyses_kind_subject", "kind", "subject"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(Text)
    subject: Mapped[str] = mapped_column(Text)  # activity id or "from..to"
    input_hash: Mapped[str] = mapped_column(Text)  # sha256(prompt version, models, context)
    model: Mapped[str] = mapped_column(Text)
    result: Mapped[Any] = mapped_column(JSON)
    created_at: Mapped[str] = mapped_column(Text, default=utcnow_iso)
