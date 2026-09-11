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

from .models import JobEvaluation, ModelInfo, RawPosting, SetupConfig

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


#: The settings screen's test button sends exactly this and expects "OK".
PROBE_PROMPT = "Rispondi solo con: OK"


class InferenceError(RuntimeError):
    """The gateway refused, timed out, or returned something unusable."""


def _normalise_probe(reply: str) -> str:
    """Strip the punctuation and markup a model wraps a one-word answer in."""
    return reply.strip().strip("*_`.!\"' \n\t").upper()


def _empty_content_reason(payload: dict[str, Any]) -> str:
    """Explain an empty completion, which usually means the budget ran out."""
    usage = payload.get("usage") or {}
    reasoning_tokens = (usage.get("completion_tokens_details") or {}).get("reasoning_tokens")
    if reasoning_tokens:
        return (
            f"the model spent all {reasoning_tokens} of its output tokens on reasoning and "
            "returned no answer — raise Max tokens or lower the reasoning effort"
        )
    finish = (payload.get("choices") or [{}])[0].get("finish_reason")
    if finish == "length":
        return "the reply was cut off by the token limit — raise Max tokens"
    return "OpenRouter returned an empty completion"


def _price(pricing: dict[str, Any], key: str) -> float | None:
    """OpenRouter quotes prices as strings of USD per token."""
    try:
        return float(pricing[key])
    except (KeyError, TypeError, ValueError):
        return None


def _parse_model(entry: dict[str, Any]) -> ModelInfo:
    pricing = entry.get("pricing") or {}
    supported = entry.get("supported_parameters") or []
    return ModelInfo(
        id=str(entry.get("id") or ""),
        name=str(entry.get("name") or entry.get("id") or ""),
        context_length=entry.get("context_length"),
        prompt_price=_price(pricing, "prompt"),
        completion_price=_price(pricing, "completion"),
        # Only models advertising the parameter honour `reasoning.effort`.
        supports_reasoning="reasoning" in supported,
    )


def filter_models(models: list[ModelInfo], query: str) -> list[ModelInfo]:
    """Case-insensitive substring match over id and display name.

    This is the `select(contains(...))` of the equivalent jq one-liner, over
    both fields so "gpt 5" finds "openai/gpt-5" by its name too.
    """
    needle = query.strip().lower()
    if not needle:
        return models
    terms = needle.split()
    return [
        model
        for model in models
        if all(term in f"{model.id} {model.name}".lower() for term in terms)
    ]


def _headers(config: SetupConfig) -> dict[str, str]:
    headers = {
        "Content-Type": "application/json",
        # OpenRouter uses these for attribution on their model leaderboards.
        "HTTP-Referer": config.openrouter.referer,
        "X-Title": config.openrouter.title,
    }
    # An empty key would build "Bearer ", which httpx rejects outright as an
    # illegal header value — so the public model catalogue became unreachable
    # before a key was ever saved, which is exactly when the picker is first
    # opened. No key simply means no Authorization header.
    key = config.openrouter.api_key.strip()
    if key:
        headers["Authorization"] = f"Bearer {key}"
    return headers


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

    def complete_raw(
        self,
        messages: list[dict[str, str]],
        *,
        structured: bool = True,
        **overrides: Any,
    ) -> dict[str, Any]:
        """POST one completion and return the whole payload.

        `structured=False` drops the JSON schema, for calls whose answer is
        prose rather than an evaluation.
        """
        if not self.config.openrouter.api_key:
            raise InferenceError("no OpenRouter API key configured")
        settings = self.config.openrouter
        body: dict[str, Any] = {
            "model": settings.model,
            "messages": messages,
            "temperature": settings.temperature,
            "max_tokens": settings.max_tokens,
        }
        if structured:
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": "job_evaluation",
                    "strict": True,
                    "schema": RESPONSE_SCHEMA,
                },
            }
        if settings.reasoning_effort != "none":
            # Models without a thinking mode ignore this; sending it to them
            # is not an error, so there is nothing to gate on here.
            body["reasoning"] = {"effort": settings.reasoning_effort}
        body.update(overrides)
        url = f"{settings.base_url.rstrip('/')}/chat/completions"
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
        if payload.get("error"):
            raise InferenceError(str(payload["error"])[:300])
        return payload

    def complete(self, messages: list[dict[str, str]], **overrides: Any) -> str:
        """The assistant text of one completion."""
        payload = self.complete_raw(messages, **overrides)
        try:
            content = payload["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise InferenceError("OpenRouter response had no completion content") from exc
        if content is None:
            raise InferenceError(_empty_content_reason(payload))
        return content

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
        """Send a real completion and check the model answers with `OK`.

        A credentials-only check passes for a model the account cannot
        actually call, a reasoning budget the model rejects, or a
        `max_tokens` so small the answer never survives the thinking phase.
        Round-tripping one tiny prompt exercises the whole configured path.
        """
        payload = self.complete_raw(
            [{"role": "user", "content": PROBE_PROMPT}], structured=False
        )
        try:
            message = payload["choices"][0]["message"]
        except (KeyError, IndexError, TypeError) as exc:
            raise InferenceError("OpenRouter response had no completion content") from exc

        reply = (message.get("content") or "").strip()
        if not reply:
            raise InferenceError(_empty_content_reason(payload))
        # Models embellish ("OK.", "**OK**"); only a wholly different answer
        # means the configured path is broken.
        if _normalise_probe(reply) != "OK":
            raise InferenceError(
                f"model answered {reply[:80]!r} instead of 'OK' — "
                "the call worked, but this model may not follow instructions well"
            )

        settings = self.config.openrouter
        usage = payload.get("usage") or {}
        detail = [f"'{payload.get('model') or settings.model}' replied OK"]
        if provider := payload.get("provider"):
            detail.append(f"via {provider}")
        if settings.reasoning_effort != "none":
            detail.append(f"reasoning={settings.reasoning_effort}")
        if total := usage.get("total_tokens"):
            reasoning_tokens = (usage.get("completion_tokens_details") or {}).get(
                "reasoning_tokens"
            )
            suffix = f" ({reasoning_tokens} reasoning)" if reasoning_tokens else ""
            detail.append(f"{total} tokens{suffix}")
        if (cost := usage.get("cost")) is not None:
            detail.append(f"${float(cost):.6f}")
        return " · ".join(detail)

    def list_models(self, query: str = "") -> list[ModelInfo]:
        """The catalogue, optionally narrowed to ids/names containing `query`."""
        url = f"{self.config.openrouter.base_url.rstrip('/')}/models"
        try:
            # The catalogue is public; the key is sent when present but is
            # not required, so the picker still works before one is saved.
            response = self._client.get(url, headers=_headers(self.config), timeout=30.0)
        except httpx.HTTPError as exc:
            raise InferenceError(f"could not reach OpenRouter: {exc}") from exc
        if response.status_code >= 400:
            raise InferenceError(f"OpenRouter returned HTTP {response.status_code}")
        try:
            entries = response.json()["data"]
        except (KeyError, TypeError, ValueError) as exc:
            raise InferenceError(
                "OpenRouter model catalogue was not in the expected shape"
            ) from exc

        models = [_parse_model(entry) for entry in entries if isinstance(entry, dict)]
        models = [model for model in models if model.id]
        return filter_models(models, query)
