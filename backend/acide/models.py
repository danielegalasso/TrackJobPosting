"""Pydantic schemas shared by the API, the inspector and the alert daemon."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

Rate = Literal["Hourly", "Daily", "Monthly", "Yearly"]
CategoryType = Literal["Direct Match", "Pivot / Growth Opportunity", "Unrelated"]
Seniority = Literal["Intern", "Junior", "Mid-Level", "Senior", "Staff", "Manager", "Director"]

SENIORITY_VALUES: tuple[str, ...] = (
    "Intern",
    "Junior",
    "Mid-Level",
    "Senior",
    "Staff",
    "Manager",
    "Director",
)
RATE_VALUES: tuple[str, ...] = ("Hourly", "Daily", "Monthly", "Yearly")
CATEGORY_TYPE_VALUES: tuple[str, ...] = (
    "Direct Match",
    "Pivot / Growth Opportunity",
    "Unrelated",
)


# ---------------------------------------------------------------------------
# Configuration (setup.json)
# ---------------------------------------------------------------------------
class OpenRouterConfig(BaseModel):
    """Credentials and routing for the inference gateway."""

    api_key: str = ""
    model: str = "google/gemini-2.5-flash"
    base_url: str = "https://openrouter.ai/api/v1"
    max_concurrency: int = Field(default=4, ge=1, le=32)
    temperature: float = Field(default=0.1, ge=0.0, le=2.0)
    referer: str = "http://localhost:8000"
    title: str = "ACIDE-Watch Portal"


class EmailConfig(BaseModel):
    """SMTP relay used to deliver alert digests."""

    enabled: bool = False
    smtp_server: str = ""
    smtp_port: int = 587
    use_tls: bool = True
    sender_email: str = ""
    sender_password: str = ""
    sender_name: str = "ACIDE-Watch"


class ScoringConfig(BaseModel):
    """Thresholds that decide what counts as worth surfacing."""

    experience_threshold: int = Field(default=75, ge=0, le=100)
    interest_threshold: int = Field(default=75, ge=0, le=100)


class TargetSource(BaseModel):
    """One company career feed to index."""

    company: str
    source_type: Literal["greenhouse", "lever", "ashby"]
    board_token: str
    enabled: bool = True


class SpiderConfig(BaseModel):
    """Politeness and scheduling controls for the job inspector."""

    enabled: bool = False
    interval_minutes: int = Field(default=360, ge=15)
    request_delay_seconds: float = Field(default=1.5, ge=0.5)
    max_jobs_per_source: int = Field(default=120, ge=1, le=1000)
    user_agent: str = (
        "ACIDE-Watch/2.0 (self-hosted job alert agent; "
        "+https://github.com/danielegalasso/TrackJobPosting)"
    )


class SetupConfig(BaseModel):
    """The whole of `setup.json`."""

    model_config = ConfigDict(validate_assignment=True)

    openrouter: OpenRouterConfig = Field(default_factory=OpenRouterConfig)
    email: EmailConfig = Field(default_factory=EmailConfig)
    scoring: ScoringConfig = Field(default_factory=ScoringConfig)
    spider: SpiderConfig = Field(default_factory=SpiderConfig)
    admin_email: str = ""
    resume_filename: str = ""
    interests: list[str] = Field(default_factory=list)
    targets: list[TargetSource] = Field(default_factory=list)


SECRET_FIELDS: tuple[tuple[str, str], ...] = (
    ("openrouter", "api_key"),
    ("email", "sender_password"),
)


# ---------------------------------------------------------------------------
# Jobs
# ---------------------------------------------------------------------------
class RawPosting(BaseModel):
    """A posting as fetched from an ATS, before any inference runs."""

    external_id: str
    company: str
    title: str
    location: str
    apply_url: str
    description: str = ""
    date_posted: str | None = None
    source_type: str = ""
    currency: str | None = None
    amount: float | None = None
    rate: Rate | None = None


class JobEvaluation(BaseModel):
    """Structured verdict returned by the dual-vector evaluator."""

    seniority: str = "Mid-Level"
    category: str = "General"
    years_experience_min: int = Field(default=0, ge=0, le=40)
    rate: str = "Yearly"
    currency: str = "USD"
    amount: float = 0.0
    experience_fit_score: int = Field(ge=0, le=100)
    interest_fit_score: int = Field(ge=0, le=100)
    category_type: str = "Unrelated"
    transferable_skills: list[str] = Field(default_factory=list)
    skills_to_learn: list[str] = Field(default_factory=list)
    alert_summary: str = ""

    @field_validator("seniority", mode="before")
    @classmethod
    def _coerce_seniority(cls, value: Any) -> str:
        return _closest(str(value or ""), SENIORITY_VALUES, "Mid-Level")

    @field_validator("rate", mode="before")
    @classmethod
    def _coerce_rate(cls, value: Any) -> str:
        return _closest(str(value or ""), RATE_VALUES, "Yearly")

    @field_validator("category_type", mode="before")
    @classmethod
    def _coerce_category_type(cls, value: Any) -> str:
        return _closest(str(value or ""), CATEGORY_TYPE_VALUES, "Unrelated")

    @field_validator("currency", mode="before")
    @classmethod
    def _coerce_currency(cls, value: Any) -> str:
        text = str(value or "USD").strip().upper()
        return text[:3] if len(text) >= 3 else "USD"

    @field_validator("experience_fit_score", "interest_fit_score", mode="before")
    @classmethod
    def _clamp_score(cls, value: Any) -> int:
        try:
            number = int(round(float(value)))
        except (TypeError, ValueError):
            return 0
        return max(0, min(100, number))

    @field_validator("amount", mode="before")
    @classmethod
    def _coerce_amount(cls, value: Any) -> float:
        try:
            return max(0.0, float(value))
        except (TypeError, ValueError):
            return 0.0

    @field_validator("transferable_skills", "skills_to_learn", mode="before")
    @classmethod
    def _coerce_skill_list(cls, value: Any) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            return [part.strip() for part in value.split(",") if part.strip()]
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
        return []

    def is_interesting(self, scoring: ScoringConfig) -> bool:
        """True when either fit vector clears its configured threshold."""
        return (
            self.experience_fit_score >= scoring.experience_threshold
            or self.interest_fit_score >= scoring.interest_threshold
        )


class Job(BaseModel):
    """A scored posting as served to the portal."""

    id: str
    title: str
    company: str
    location: str
    seniority: str = "Mid-Level"
    category: str = "General"
    years_experience_min: int = 0
    date_posted: str | None = None
    rate: str = "Yearly"
    currency: str = "USD"
    amount: float = 0.0
    experience_fit_score: int = 0
    interest_fit_score: int = 0
    category_type: str = "Unrelated"
    transferable_skills: list[str] = Field(default_factory=list)
    skills_to_learn: list[str] = Field(default_factory=list)
    alert_summary: str = ""
    apply_url: str
    source_type: str = ""
    saved: bool = False
    dismissed: bool = False
    created_at: str | None = None


class JobPage(BaseModel):
    """One page of the results grid."""

    items: list[Job]
    total: int
    limit: int
    offset: int
    has_more: bool


class JobFacets(BaseModel):
    """Distinct values backing the filter dropdowns."""

    categories: list[str] = Field(default_factory=list)
    companies: list[str] = Field(default_factory=list)
    seniorities: list[str] = Field(default_factory=list)
    currencies: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Alerts
# ---------------------------------------------------------------------------
class AlertFilters(BaseModel):
    """The filter state captured when an alert is created."""

    search: str | None = None
    location: str | None = None
    remote: str | None = None
    category: str | None = None
    seniority: str | None = None
    company: str | None = None
    years_experience: str | None = None
    posted_within: str | None = None
    rate: str | None = None
    currency: str | None = None
    min_amount: float | None = None
    match_posting_currency: bool = False


class AlertCreate(BaseModel):
    email: EmailStr
    filters: AlertFilters = Field(default_factory=AlertFilters)


class AlertSubscription(BaseModel):
    id: int
    email: str
    filters: AlertFilters
    active: bool
    created_at: str | None = None
    last_sent_at: str | None = None


# ---------------------------------------------------------------------------
# Spider / operations
# ---------------------------------------------------------------------------
class SpiderRunSummary(BaseModel):
    started_at: datetime
    finished_at: datetime | None = None
    sources_polled: int = 0
    postings_seen: int = 0
    postings_new: int = 0
    postings_scored: int = 0
    alerts_sent: int = 0
    errors: list[str] = Field(default_factory=list)
    running: bool = False


class HandshakeResult(BaseModel):
    ok: bool
    detail: str


def _closest(value: str, allowed: tuple[str, ...], fallback: str) -> str:
    """Map free-form model output onto a closed vocabulary.

    LLMs answer "senior" or "Senior Engineer" where the schema wants
    "Senior"; anything genuinely unrecognised falls back rather than
    failing the whole evaluation.
    """
    text = value.strip()
    if not text:
        return fallback
    lowered = text.lower()
    for option in allowed:
        if lowered == option.lower():
            return option
    for option in allowed:
        if option.lower() in lowered or lowered in option.lower():
            return option
    return fallback
