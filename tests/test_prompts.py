"""Behavioral tests for the correction prompt.

The prompt is the only thing that turns a rejection into a fix, so what it says
about *why* an attempt was rejected decides whether the loop converges.
"""

from __future__ import annotations

import pytest

from video_pipeline.provider import ProviderRequest
from video_pipeline.reference_catalog import SOURCE_COMMIT

try:
    from video_pipeline.prompts import build_prompt
except (ImportError, ModuleNotFoundError):
    build_prompt = None  # type: ignore[assignment]


SEMANTIC_REASON = (
    "frame 10 shows 2 shapes (square, square) but the scene declares at most 1"
)
BEAT_REASON = "beat 3 was never observed: expected a square moved right"


def _require_contract() -> None:
    if build_prompt is None:
        pytest.fail("PROMPT_CONTRACT_MISSING")


def _request(**diagnostics: object) -> ProviderRequest:
    return ProviderRequest(
        schema_version="1.0",
        scene_name="AcceptanceScene",
        description="circle, then square, then right",
        previous_code="PREVIOUS_CODE_SENTINEL",
        diagnostics=diagnostics,
    )


def _semantic_request() -> ProviderRequest:
    """A render that succeeded but produced the wrong video."""

    return _request(
        exit_code=0,
        timeout=False,
        stdout="Rendered AcceptanceScene",
        stderr="",
        validator_reasons=[SEMANTIC_REASON, BEAT_REASON],
    )


def _crash_request() -> ProviderRequest:
    return _request(
        exit_code=1,
        timeout=False,
        stdout="",
        stderr="Traceback\n❱ 14 │ PREVIOUS_CODE_SENTINEL\nNameError: boom",
        validator_reasons=["MP4 is missing"],
    )


def test_a_semantic_rejection_is_not_described_as_a_render_failure() -> None:
    """Manim exited zero, so telling the model to make it render is a lie.

    Observed in run 6c63fea151564d9f932b98177d26811e: five attempts produced
    byte-identical code because the closing instruction read "Fix the failure
    above so Manim renders the scene" while the render had already succeeded.
    """

    _require_contract()
    prompt = build_prompt(_semantic_request())

    closing = prompt.splitlines()[-1]
    assert "so Manim renders the scene" not in closing
    assert "FAILED" not in prompt.splitlines()[0]


def test_a_semantic_rejection_names_the_observed_problem_in_the_closing_line() -> None:
    """The last instruction must carry the reason, not a generic one."""

    _require_contract()
    closing = build_prompt(_semantic_request()).splitlines()[-1]

    assert SEMANTIC_REASON in closing or BEAT_REASON in closing


def test_a_semantic_rejection_says_the_render_succeeded() -> None:
    """The model must know the code runs and the picture is what is wrong."""

    _require_contract()
    prompt = build_prompt(_semantic_request())

    assert "rendered" in prompt.lower()
    assert SEMANTIC_REASON in prompt
    assert BEAT_REASON in prompt


def test_a_crash_still_leads_with_the_traceback_error() -> None:
    """A real crash keeps the existing behaviour."""

    _require_contract()
    prompt = build_prompt(_crash_request())

    assert "NameError: boom" in prompt
    assert "NameError: boom" in prompt.splitlines()[-1]


def test_a_first_attempt_carries_no_failure_language() -> None:
    """Without diagnostics the prompt is a plain generation request."""

    _require_contract()
    prompt = build_prompt(
        ProviderRequest(
            schema_version="1.0",
            scene_name="AcceptanceScene",
            description="circle",
        )
    )

    assert "FAILED" not in prompt
    assert "rejected" not in prompt.lower()


def test_topic_reference_carries_immutable_provenance_and_community_code() -> None:
    """A selected adaptation must remain traceable to one upstream revision."""

    _require_contract()
    prompt = build_prompt(
        ProviderRequest(
            schema_version="1.0",
            scene_name="LinearAlgebraScene",
            description="Transforme os vetores da base com uma matriz.",
            topics=("linear_algebra",),
            reference_examples=1,
        )
    )

    assert SOURCE_COMMIT in prompt
    assert "_2016/eola/chapter3.py::FollowIHatJHat" in prompt
    assert "from manim import" in prompt
    assert "manim_imports_ext" not in prompt


def test_reference_limit_is_filled_with_complementary_examples_for_one_topic() -> None:
    """Requesting two algebra examples should not silently return only one."""

    _require_contract()
    prompt = build_prompt(
        ProviderRequest(
            schema_version="1.0",
            scene_name="LinearAlgebraScene",
            description="Mostre uma transformação e uma rede como matrizes.",
            topics=("linear_algebra",),
            reference_examples=2,
        )
    )

    assert prompt.count("Provenance: https://github.com/3b1b/videos/blob/") == 2
    assert "Reference linear-map-basis" in prompt
    assert "Reference neural-network-layers" in prompt


def test_the_closing_line_carries_every_semantic_reason() -> None:
    """One reason names the picture, another names the code change. Both matter.

    Observed in run 890ea538ff3a4ee7b187ce87625f76f7: the closing line carried
    only the beat message, with coordinates, while the prescriptive reason that
    names the exact code fix was left buried mid-prompt.
    """

    _require_contract()
    closing = build_prompt(_semantic_request()).splitlines()[-1]

    assert SEMANTIC_REASON in closing
    assert BEAT_REASON in closing


def test_prompts_audit_contract() -> None:
    """Inventory the prompt evidence contract without production imports."""

    assert callable(globals().get("test_a_semantic_rejection_is_not_described_as_a_render_failure"))
    assert callable(globals().get("test_a_crash_still_leads_with_the_traceback_error"))
