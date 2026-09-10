"""The dual-vector evaluator: prompt shape, parsing, and hostile output."""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from acide.llm import (
    PROBE_PROMPT,
    InferenceError,
    OpenRouterClient,
    build_prompt,
    filter_models,
    parse_response,
)
from acide.models import (
    JobEvaluation,
    ModelInfo,
    OpenRouterConfig,
    RawPosting,
    ScoringConfig,
    SetupConfig,
)

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
def test_handshake_sends_the_probe_and_accepts_ok():
    """The test button runs a real completion, not a credentials check."""
    route = respx.post("https://openrouter.ai/api/v1/chat/completions").mock(
        return_value=httpx.Response(
            200,
            json={
                "model": "openai/gpt-5.6-luna",
                "provider": "OpenAI",
                "choices": [{"message": {"content": "OK"}, "finish_reason": "stop"}],
                "usage": {"total_tokens": 18, "cost": 0.0000086},
            },
        )
    )
    with OpenRouterClient(_config(model="openai/gpt-5.6-luna")) as client:
        detail = client.handshake()

    body = json.loads(route.calls[0].request.content)
    assert body["messages"] == [{"role": "user", "content": PROBE_PROMPT}]
    # The probe asks for prose, so the evaluation schema must not be attached.
    assert "response_format" not in body
    assert "openai/gpt-5.6-luna" in detail
    assert "OpenAI" in detail
    assert "18 tokens" in detail
    assert "$0.000009" in detail


@respx.mock
@pytest.mark.parametrize("reply", ["OK", " ok ", "**OK.**", "`OK`"])
def test_handshake_tolerates_a_decorated_ok(reply):
    respx.post("https://openrouter.ai/api/v1/chat/completions").mock(
        return_value=httpx.Response(200, json={"choices": [{"message": {"content": reply}}]})
    )
    with OpenRouterClient(_config()) as client:
        assert "replied OK" in client.handshake()


@respx.mock
def test_handshake_fails_when_the_model_answers_something_else():
    respx.post("https://openrouter.ai/api/v1/chat/completions").mock(
        return_value=httpx.Response(
            200, json={"choices": [{"message": {"content": "Certainly! Here you go."}}]}
        )
    )
    with OpenRouterClient(_config()) as client, pytest.raises(InferenceError, match="instead of 'OK'"):
        client.handshake()


@respx.mock
def test_handshake_explains_a_reply_eaten_by_reasoning_tokens():
    """A reasoning model can spend the whole budget before answering."""
    respx.post("https://openrouter.ai/api/v1/chat/completions").mock(
        return_value=httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": ""}, "finish_reason": "length"}],
                "usage": {
                    "total_tokens": 512,
                    "completion_tokens_details": {"reasoning_tokens": 500},
                },
            },
        )
    )
    with OpenRouterClient(_config()) as client, pytest.raises(InferenceError, match="raise Max tokens"):
        client.handshake()


@respx.mock
def test_reasoning_effort_is_sent_only_when_set():
    route = respx.post("https://openrouter.ai/api/v1/chat/completions").mock(
        return_value=httpx.Response(200, json={"choices": [{"message": {"content": "OK"}}]})
    )
    with OpenRouterClient(_config(reasoning_effort="none")) as client:
        client.handshake()
    assert "reasoning" not in json.loads(route.calls[0].request.content)

    with OpenRouterClient(_config(reasoning_effort="max")) as client:
        client.handshake()
    body = json.loads(route.calls[1].request.content)
    assert body["reasoning"] == {"effort": "max"}


@respx.mock
def test_max_tokens_is_sent_on_every_call():
    route = respx.post("https://openrouter.ai/api/v1/chat/completions").mock(
        return_value=httpx.Response(200, json={"choices": [{"message": {"content": "OK"}}]})
    )
    with OpenRouterClient(_config(max_tokens=4096)) as client:
        client.handshake()
    assert json.loads(route.calls[0].request.content)["max_tokens"] == 4096


@respx.mock
def test_evaluation_carries_the_reasoning_settings_too():
    """Scoring must use the same configured budget as the test button."""
    route = respx.post("https://openrouter.ai/api/v1/chat/completions").mock(
        return_value=httpx.Response(
            200, json={"choices": [{"message": {"content": json.dumps(VALID)}}]}
        )
    )
    with OpenRouterClient(_config(reasoning_effort="high", max_tokens=8000)) as client:
        client.evaluate(_posting(), "cv")

    body = json.loads(route.calls[0].request.content)
    assert body["reasoning"] == {"effort": "high"}
    assert body["max_tokens"] == 8000
    # Evaluations keep the strict schema; only the probe drops it.
    assert body["response_format"]["type"] == "json_schema"


@respx.mock
def test_list_models_maps_the_catalogue():
    respx.get("https://openrouter.ai/api/v1/models").mock(
        return_value=httpx.Response(
            200,
            json={
                "data": [
                    {
                        "id": "openai/gpt-5.6-luna",
                        "name": "OpenAI: GPT-5.6 Luna",
                        "context_length": 400000,
                        "pricing": {"prompt": "0.0000002", "completion": "0.0000012"},
                        "supported_parameters": ["max_tokens", "reasoning"],
                    },
                    {
                        "id": "google/gemini-2.5-flash",
                        "name": "Google: Gemini 2.5 Flash",
                        "context_length": 1048576,
                        "pricing": {"prompt": "0.0000003", "completion": "0.0000025"},
                        "supported_parameters": ["max_tokens"],
                    },
                ]
            },
        )
    )
    with OpenRouterClient(_config()) as client:
        models = client.list_models()

    assert [model.id for model in models] == ["openai/gpt-5.6-luna", "google/gemini-2.5-flash"]
    assert models[0].supports_reasoning is True
    assert models[1].supports_reasoning is False
    assert models[0].context_length == 400000
    assert models[0].prompt_price == pytest.approx(0.0000002)


@respx.mock
def test_list_models_filters_like_the_jq_one_liner():
    respx.get("https://openrouter.ai/api/v1/models").mock(
        return_value=httpx.Response(
            200,
            json={
                "data": [
                    {"id": "openai/gpt-5.6-luna", "name": "OpenAI: GPT-5.6 Luna"},
                    {"id": "google/gemini-2.5-flash", "name": "Google: Gemini 2.5 Flash"},
                    {"id": "anthropic/claude-opus-5", "name": "Anthropic: Claude Opus 5"},
                ]
            },
        )
    )
    with OpenRouterClient(_config()) as client:
        assert [m.id for m in client.list_models("gpt")] == ["openai/gpt-5.6-luna"]
        assert [m.id for m in client.list_models("CLAUDE")] == ["anthropic/claude-opus-5"]
        assert len(client.list_models("")) == 3


def test_filter_models_matches_id_and_name_across_terms():
    models = [
        ModelInfo(id="openai/gpt-5.6-luna", name="OpenAI: GPT-5.6 Luna"),
        ModelInfo(id="openai/gpt-5.6-mini", name="OpenAI: GPT-5.6 Mini"),
        ModelInfo(id="google/gemini-2.5-flash", name="Google: Gemini 2.5 Flash"),
    ]
    # Every term must match, so two words narrow rather than widen.
    assert [m.id for m in filter_models(models, "gpt mini")] == ["openai/gpt-5.6-mini"]
    # A name-only term still finds the model whose id does not contain it.
    assert [m.id for m in filter_models(models, "google")] == ["google/gemini-2.5-flash"]
    assert filter_models(models, "nonexistent") == []


def test_reasoning_effort_falls_back_on_an_unknown_value():
    assert OpenRouterConfig(reasoning_effort="MAX").reasoning_effort == "max"
    assert OpenRouterConfig(reasoning_effort="turbo").reasoning_effort == "none"
    assert OpenRouterConfig().reasoning_effort == "none"
