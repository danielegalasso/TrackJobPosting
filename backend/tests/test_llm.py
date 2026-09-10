"""The dual-vector evaluator: prompt shape, parsing, and hostile output."""

from __future__ import annotations

import httpx
import pytest
import respx

from acide.llm import InferenceError, OpenRouterClient, build_prompt, parse_response
from acide.models import JobEvaluation, OpenRouterConfig, RawPosting, ScoringConfig, SetupConfig

VALID = {
    "seniority": "Senior",
    "category": "Cloud Security",
    "years_experience_min": 6,
    "rate": "Yearly",
    "currency": "USD",
    "amount": 210000,
    "experience_fit_score": 68,
    "interest_fit_score": 96,
    "category_type": "Pivot / Growth Opportunity",
    "transferable_skills": ["Python", "Incident response"],
    "skills_to_learn": ["AWS GuardDuty", "Detection engineering"],
    "alert_summary": "Strong pivot into cloud security. You would need detection tooling depth.",
}


def _config(**overrides) -> SetupConfig:
    return SetupConfig(
        openrouter=OpenRouterConfig(api_key="sk-test", **overrides),
        interests=["Cloud security", "Detection engineering"],
    )


def _posting() -> RawPosting:
    return RawPosting(
        external_id="1",
        company="Globex",
        title="Cloud Security Engineer",
        location="Sunnyvale, California",
        apply_url="https://example.com/1",
        description="Detection and response at scale.",
        source_type="greenhouse",
    )


def test_prompt_carries_cv_interests_and_posting():
    prompt = build_prompt(_posting(), _config(), "Ten years of embedded C.")
    assert "Ten years of embedded C." in prompt
    assert "- Cloud security" in prompt
    assert "Cloud Security Engineer" in prompt
    assert "Globex" in prompt
    # Both vectors must be described, or the model collapses them into one.
    assert "experience_fit_score" in prompt
    assert "interest_fit_score" in prompt


def test_prompt_truncates_a_huge_description():
    posting = _posting()
    posting.description = "x" * 50_000
    assert len(build_prompt(posting, _config(), "y" * 50_000)) < 20_000


@pytest.mark.parametrize(
    "content",
    [
        '{"experience_fit_score": 70, "interest_fit_score": 20}',
        '```json\n{"experience_fit_score": 70, "interest_fit_score": 20}\n```',
        'Sure! Here is the JSON:\n{"experience_fit_score": 70, "interest_fit_score": 20}\nHope that helps.',
    ],
)
def test_parse_response_survives_model_chattiness(content):
    assert parse_response(content)["experience_fit_score"] == 70


def test_parse_response_rejects_non_json():
    with pytest.raises(InferenceError):
        parse_response("I cannot evaluate this posting.")


def test_evaluation_coerces_out_of_vocabulary_output():
    """Models answer 'senior engineer' where the schema wants 'Senior'."""
    evaluation = JobEvaluation(
        **{**VALID, "seniority": "senior engineer", "rate": "per year", "currency": "usd"}
    )
    assert evaluation.seniority == "Senior"
    assert evaluation.rate == "Yearly"
    assert evaluation.currency == "USD"


def test_evaluation_clamps_scores_out_of_range():
    evaluation = JobEvaluation(
        **{**VALID, "experience_fit_score": 140, "interest_fit_score": -20}
    )
    assert evaluation.experience_fit_score == 100
    assert evaluation.interest_fit_score == 0


def test_evaluation_accepts_comma_separated_skill_strings():
    evaluation = JobEvaluation(**{**VALID, "transferable_skills": "Python, Go , Rust"})
    assert evaluation.transferable_skills == ["Python", "Go", "Rust"]


def test_is_interesting_honours_either_vector():
    scoring = ScoringConfig(experience_threshold=75, interest_threshold=75)
    assert JobEvaluation(**{**VALID, "experience_fit_score": 80, "interest_fit_score": 5}).is_interesting(scoring)
    assert JobEvaluation(**{**VALID, "experience_fit_score": 5, "interest_fit_score": 80}).is_interesting(scoring)
    assert not JobEvaluation(**{**VALID, "experience_fit_score": 50, "interest_fit_score": 50}).is_interesting(scoring)


@respx.mock
def test_evaluate_calls_openrouter_and_returns_a_verdict():
    route = respx.post("https://openrouter.ai/api/v1/chat/completions").mock(
        return_value=httpx.Response(
            200,
            json={"choices": [{"message": {"content": '{"seniority":"Senior","category":"Cloud Security","years_experience_min":6,"experience_fit_score":68,"interest_fit_score":96,"category_type":"Pivot / Growth Opportunity","transferable_skills":["Python"],"skills_to_learn":["GuardDuty"],"alert_summary":"Good pivot."}'}}]},
        )
    )
    with OpenRouterClient(_config()) as client:
        evaluation = client.evaluate(_posting(), "Ten years of embedded C.")

    assert evaluation.interest_fit_score == 96
    assert evaluation.category_type == "Pivot / Growth Opportunity"

    request = route.calls[0].request
    assert request.headers["Authorization"] == "Bearer sk-test"
    assert request.headers["X-Title"] == "ACIDE-Watch Portal"


@respx.mock
@pytest.mark.parametrize(
    ("status", "needle"),
    [
        (401, "rejected the API key"),
        (402, "insufficient credit"),
        (429, "rate limit"),
        (500, "HTTP 500"),
    ],
)
def test_gateway_errors_are_actionable(status, needle):
    respx.post("https://openrouter.ai/api/v1/chat/completions").mock(
        return_value=httpx.Response(status, json={"error": "nope"})
    )
    with OpenRouterClient(_config()) as client, pytest.raises(InferenceError, match=needle):
        client.evaluate(_posting(), "cv")


def test_missing_api_key_is_caught_before_the_request():
    config = SetupConfig()
    with OpenRouterClient(config) as client, pytest.raises(InferenceError, match="no OpenRouter API key"):
        client.evaluate(_posting(), "cv")


@respx.mock
def test_handshake_reports_the_active_model():
    respx.get("https://openrouter.ai/api/v1/key").mock(
        return_value=httpx.Response(200, json={"data": {"limit": None, "usage": 0.42}})
    )
    with OpenRouterClient(_config(model="google/gemini-2.5-flash")) as client:
        detail = client.handshake()
    assert "google/gemini-2.5-flash" in detail
    assert "unlimited" in detail
