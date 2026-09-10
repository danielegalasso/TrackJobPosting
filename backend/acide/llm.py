"""OpenRouter inference client and the dual-vector fit evaluator.

Two independent scores come back for every posting:

* `experience_fit_score` — how well the candidate's *proven* history maps
  onto the role as written.
* `interest_fit_score` — how well the role serves the candidate's stated
  *pivot* ambitions, regardless of whether they can already do the job.

Keeping them separate is the whole point: a role can be a 95% experience
match and a 20% interest match (a lateral move), or the reverse (a stretch
into a new field). Collapsing them into one number loses the distinction the
portal is built to show.
"""

from __future__ import annotations

import json
import re
from typing import Any

import httpx

from .models import JobEvaluation, RawPosting, SetupConfig

RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "seniority": {
            "type": "string",
            "enum": ["Intern", "Junior", "Mid-Level", "Senior", "Staff", "Manager", "Director"],
        },
        "category": {"type": "string"},
        "years_experience_min": {"type": "integer"},
        "rate": {"type": "string", "enum": ["Hourly", "Daily", "Monthly", "Yearly"]},
        "currency": {"type": "string"},
        "amount": {"type": "number"},
        "experience_fit_score": {"type": "integer"},
        "interest_fit_score": {"type": "integer"},
        "category_type": {
            "type": "string",
            "enum": ["Direct Match", "Pivot / Growth Opportunity", "Unrelated"],
        },
        "transferable_skills": {"type": "array", "items": {"type": "string"}},
        "skills_to_learn": {"type": "array", "items": {"type": "string"}},
        "alert_summary": {"type": "string"},
    },
    "required": [
        "seniority",
        "category",
        "years_experience_min",
        "experience_fit_score",
        "interest_fit_score",
        "category_type",
        "transferable_skills",
        "skills_to_learn",
        "alert_summary",
    ],
    "additionalProperties": False,
}

SYSTEM_PROMPT = (
    "You are a precise technical recruiter. You read one job posting and one "
    "candidate profile, then return a strict JSON verdict. You never invent "
    "compensation figures: if the posting does not state pay, return amount 0. "
    "You score two vectors independently and you are willing to give low scores."
)

PROMPT_TEMPLATE = """Evaluate this job opening for the candidate below.

## CANDIDATE — PROVEN BACKGROUND (from their CV)
{cv_text}

## CANDIDATE — STATED PIVOT INTERESTS
{interests}

## JOB POSTING
Company: {company}
Title: {title}
Location: {location}
Published: {date_posted}

{description}

## SCORING RULES
- experience_fit_score (0-100): how much of THIS role the candidate can
  already do, based only on the proven background. A senior role needing
  skills they have never shipped scores low even if they want it.
- interest_fit_score (0-100): how strongly this role advances the STATED
  PIVOT INTERESTS. A role they can already do but do not care about scores
  low here.
- category_type: "Direct Match" when experience fit leads; "Pivot / Growth
  Opportunity" when interest fit leads and the gap is bridgeable;
  "Unrelated" when neither vector is meaningful.
- years_experience_min: the minimum years the posting itself asks for; 0 if
  it does not say.
- amount / currency / rate: only if the posting states compensation.
  Otherwise amount 0.
- transferable_skills: what the candidate already has that this role wants.
- skills_to_learn: the concrete gaps they would need to close.
- alert_summary: two sentences, addressed to the candidate, explaining why
  this is or is not worth their time.

Return ONLY the JSON object."""


class InferenceError(RuntimeError):
    """The gateway refused, timed out, or returned something unusable."""


def _headers(config: SetupConfig) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {config.openrouter.api_key}",
        "Content-Type": "application/json",
        # OpenRouter uses these for attribution on their model leaderboards.
        "HTTP-Referer": config.openrouter.referer,
        "X-Title": config.openrouter.title,
    }


def build_prompt(posting: RawPosting, config: SetupConfig, cv_text: str) -> str:
    interests = "\n".join(f"- {item}" for item in config.interests) or "- (none stated)"
    return PROMPT_TEMPLATE.format(
        cv_text=(cv_text or "(no CV uploaded)")[:6000],
        interests=interests,
        company=posting.company,
        title=posting.title,
        location=posting.location or "Not specified",
        date_posted=posting.date_posted or "unknown",
        description=(posting.description or "(no description published)")[:8000],
    )


_JSON_BLOCK_RE = re.compile(r"\{.*\}", re.DOTALL)


def parse_response(content: str) -> dict[str, Any]:
    """Pull a JSON object out of a completion.

    Models occasionally wrap the object in prose or a fenced code block even
    when asked for raw JSON, so fall back to the outermost braces.
    """
    text = (content or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
        text = re.sub(r"\n?```$", "", text).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = _JSON_BLOCK_RE.search(text)
        if not match:
            raise InferenceError("model returned no JSON object") from None
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError as exc:
            raise InferenceError(f"model returned malformed JSON: {exc}") from exc


class OpenRouterClient:
    """Thin, dependency-light wrapper over the OpenRouter chat completions API."""

    def __init__(self, config: SetupConfig, client: httpx.Client | None = None) -> None:
        self.config = config
        self._client = client or httpx.Client(timeout=httpx.Timeout(120.0, connect=15.0))
        self._owns_client = client is None

    def __enter__(self) -> OpenRouterClient:
        return self

    def __exit__(self, *exc_info: Any) -> None:
        self.close()

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def complete(self, messages: list[dict[str, str]], **overrides: Any) -> str:
        if not self.config.openrouter.api_key:
            raise InferenceError("no OpenRouter API key configured")
        body: dict[str, Any] = {
            "model": self.config.openrouter.model,
            "messages": messages,
            "temperature": self.config.openrouter.temperature,
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "job_evaluation",
                    "strict": True,
                    "schema": RESPONSE_SCHEMA,
                },
            },
        }
        body.update(overrides)
        url = f"{self.config.openrouter.base_url.rstrip('/')}/chat/completions"
        try:
            response = self._client.post(url, headers=_headers(self.config), json=body)
        except httpx.HTTPError as exc:
            raise InferenceError(f"OpenRouter request failed: {exc}") from exc

        if response.status_code == 401:
            raise InferenceError("OpenRouter rejected the API key (401)")
        if response.status_code == 402:
            raise InferenceError("OpenRouter reports insufficient credit (402)")
        if response.status_code == 429:
            raise InferenceError("OpenRouter rate limit reached (429)")
        if response.status_code >= 400:
            raise InferenceError(
                f"OpenRouter returned HTTP {response.status_code}: {response.text[:300]}"
            )

        payload = response.json()
        if "error" in payload and payload["error"]:
            raise InferenceError(str(payload["error"])[:300])
        try:
            return payload["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise InferenceError("OpenRouter response had no completion content") from exc

    def evaluate(self, posting: RawPosting, cv_text: str) -> JobEvaluation:
        """Score one posting on both fit vectors."""
        content = self.complete(
            [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": build_prompt(posting, self.config, cv_text)},
            ]
        )
        return JobEvaluation(**parse_response(content))

    def handshake(self) -> str:
        """Verify credentials cheaply, for the settings screen's test button."""
        if not self.config.openrouter.api_key:
            raise InferenceError("no OpenRouter API key configured")
        url = f"{self.config.openrouter.base_url.rstrip('/')}/key"
        try:
            response = self._client.get(url, headers=_headers(self.config), timeout=20.0)
        except httpx.HTTPError as exc:
            raise InferenceError(f"could not reach OpenRouter: {exc}") from exc
        if response.status_code == 401:
            raise InferenceError("OpenRouter rejected the API key (401)")
        if response.status_code >= 400:
            raise InferenceError(f"OpenRouter returned HTTP {response.status_code}")
        data = response.json().get("data", {})
        limit = data.get("limit")
        usage = data.get("usage")
        budget = "unlimited" if limit is None else f"{usage or 0:.4f} of {limit} used"
        return f"Key accepted by OpenRouter; model '{self.config.openrouter.model}' ({budget})."
