"""Deterministic prompt text used by the render-in-the-loop pipeline.

This module is the single source of truth for what is sent to the provider.
The pipeline preserves the very same text as ``prompt.txt`` for each attempt,
so the stored artifact reproduces the request byte for byte.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import TYPE_CHECKING

from video_pipeline.reference_catalog import select_reference_examples

if TYPE_CHECKING:  # pragma: no cover - import cycle guard, not runtime behavior
    from video_pipeline.provider import ProviderRequest

_BASE_INSTRUCTIONS = (
    "Generate only valid Python for Manim Community 0.21.0.\n"
    "Use the requested scene class name and return a complete source file.\n"
    "Implement every step of the description, in the order it is written, as its\n"
    "own self.play(...) animation. Never omit, merge, reorder, or substitute a\n"
    "step, and construct each named shape with its own Manim class.\n"
    "Manim Community rules that decide whether the render is correct:\n"
    "- After self.play(Transform(a, b)) the mobject on screen is `a`, not `b`.\n"
    "  Every later step must animate `a`; animating `b` adds a second shape.\n"
    "- Prefer self.play(mobject.animate.shift(...)) to move something.\n"
    "- MoveToTarget(m) is only valid after m.generate_target().\n"
    "Do not include commentary outside the Python source."
)

# A local 7B model drowns in a full Rich traceback.  Keep the decisive tail of
# each stream bounded so the failing line stays inside the model's attention.
_STREAM_TAIL_LINES = 40
_STREAM_TAIL_CHARS = 4000

# Rendered in this order so the failing cause precedes the raw process streams.
_DIAGNOSTIC_ORDER = ("exit_code", "timeout", "timed_out", "argv", "validator_reasons")
_STREAM_KEYS = ("stdout", "stderr")

# Rich marks the failing frame line with this glyph.
_TRACEBACK_MARKER = "\u2771"

# A reason mentioning the scene source tells the model what to edit.
_NAMES_A_CODE_CHANGE = "scene animates"
_QUALITY_KEYS = ("quality_report", "quality_findings")


def build_prompt(request: ProviderRequest) -> str:
    """Build the generation or correction prompt for one provider request."""

    lines = [
        _BASE_INSTRUCTIONS,
        "Scene specification:",
        f"scene_name: {request.scene_name}",
        f"description: {request.description}",
        f"Use VisualScene and define the requested class as class "
        f"{request.scene_name}(VisualScene).",
    ]
    temporal_context = _temporal_context_lines(request)
    if temporal_context:
        lines.extend(["Temporal scene context:", *temporal_context])
    if request.theme is not None:
        lines.extend(
            [
                "Visual identity contract (use semantic roles, never scene-local hex values):",
                json.dumps(request.theme, ensure_ascii=False, sort_keys=True),
            ]
        )
    if request.scene_plan is not None:
        lines.extend(
            [
                "ScenePlan (the contract exists before this Python source):",
                json.dumps(request.scene_plan, ensure_ascii=False, sort_keys=True),
                "Use VisualScene, register every planned object with its semantic ID, "
                "and checkpoint each beat. Preserve the plan's timing and continuity.",
            ]
        )
    if request.capabilities:
        lines.append(
            "Capabilities authorized for this scene only: "
            + ", ".join(request.capabilities)
        )
        if request.capability_context:
            lines.extend(
                [
                    "Proven helper contracts for these capabilities:",
                    json.dumps(
                        list(request.capability_context),  # type: ignore[misc]
                        ensure_ascii=False,
                        sort_keys=True,
                    ),
                ]
            )
    if request.topics:
        lines.append(f"topics: {', '.join(request.topics)}")
    if request.expectations:
        lines.extend(
            [
                "Machine-verifiable expectations (implement all exact values):",
                json.dumps(request.expectations, ensure_ascii=False, sort_keys=True),
            ]
        )
        latex = request.expectations.get("latex")
        if isinstance(latex, list) and latex:
            lines.append(
                "For every latex item, use MathTex(tex, font_size=font_size, color=COLOR) "
                ".move_to([x, y, 0]) and leave it fully visible in at least one frame."
            )
        text = request.expectations.get("text")
        if isinstance(text, list) and text:
            lines.append(
                "For every text item, use exactly its renderer (Text or Tex), content, "
                "font when present, font_size, color and .move_to([x, y, 0]); leave it "
                "fully visible in at least one frame."
            )
    examples = select_reference_examples(
        request.topics,
        limit=request.reference_examples,
    )
    if examples:
        lines.extend(
            [
                "Authorized 3b1b-derived Manim Community reference patterns follow.",
                "Adapt their technique; keep the requested scene class name and specification.",
            ]
        )
        for example in examples:
            lines.extend(
                [
                    f"Reference {example.identifier}: {example.title}",
                    f"Provenance: {example.source_url}::{example.source_scene}",
                    "```python",
                    example.code.rstrip(),
                    "```",
                ]
            )
    if request.diagnostics is None:
        lines.append("Return the complete Python source without commentary.")
        return "\n".join(lines)

    # The diagnosis precedes the rejected candidate, and the corrective
    # instruction closes the prompt.  A small local model follows the last
    # instruction it read, so the failing line must not be buried mid-prompt.
    failing = _failing_source_lines(
        request.diagnostics.get("stderr"), request.previous_code
    )
    lines.extend(_diagnostic_lines(request.diagnostics, failing))
    if request.previous_code is not None:
        rejected = _rendered_successfully(request.diagnostics)
        headline = (
            "This is the code that produced the rejected video."
            if rejected
            else "This is the code that failed."
        )
        lines.extend(
            [
                f"{headline} Rewrite it; never return it unchanged:",
                "```python",
                request.previous_code,
                "```",
            ]
        )
    lines.append(_corrective_instruction(request.diagnostics, failing))
    return "\n".join(lines)


def _temporal_context_lines(request: ProviderRequest) -> list[str]:
    """Render authored timing and adjacent-scene context when supplied."""

    lines: list[str] = []
    if request.narration_text is not None:
        lines.append(f"narration_text: {request.narration_text}")
    if request.start_seconds is not None:
        lines.append(f"start_seconds: {request.start_seconds}")
    if request.end_seconds is not None:
        lines.append(f"end_seconds: {request.end_seconds}")
    if request.target_duration_seconds is not None:
        lines.append(f"target_duration_seconds: {request.target_duration_seconds}")
    if request.objective is not None:
        lines.append(f"objective: {request.objective}")
    if request.previous_scene is not None:
        lines.append(
            "previous_scene: "
            + json.dumps(request.previous_scene, ensure_ascii=False, sort_keys=True)
        )
    if request.next_scene is not None:
        lines.append(
            "next_scene: "
            + json.dumps(request.next_scene, ensure_ascii=False, sort_keys=True)
        )
    if request.required_objects:
        lines.append(
            "required_objects: "
            + str(
                json.dumps(
                    list(request.required_objects),  # type: ignore[misc]
                    ensure_ascii=False,
                )
            )
        )
    if request.required_elements:
        lines.append(
            "required_elements: "
            + str(
                json.dumps(
                    list(request.required_elements),  # type: ignore[misc]
                    ensure_ascii=False,
                )
            )
        )
    if request.resolution is not None:
        lines.append(
            "resolution: "
            + str(
                json.dumps(
                    list(request.resolution),  # type: ignore[misc]
                    ensure_ascii=False,
                )
            )
        )
    if request.fps is not None:
        lines.append(f"fps: {request.fps}")
    if request.prior_findings:
        lines.append(
            "prior_findings: "
            + json.dumps(request.prior_findings, ensure_ascii=False, sort_keys=True)
        )
    return lines


def build_generation_prompt(request: ProviderRequest) -> str:
    """Build the first-pass generation prompt."""

    return build_prompt(request)


def build_correction_prompt(request: ProviderRequest) -> str:
    """Build a correction prompt containing the prior code and diagnostics."""

    if request.previous_code is None or request.diagnostics is None:
        raise ValueError("correction prompt requires previous code and diagnostics")
    return build_prompt(request)


def _diagnostic_lines(
    diagnostics: Mapping[str, object],
    failing: list[str],
) -> list[str]:
    """Render every diagnostic fact with the decisive failure first."""

    lines = [_headline(diagnostics)]
    if failing:
        lines.append("Manim failed on these lines of the code you generated:")
        lines.extend(failing)
    error = _root_error(diagnostics.get("stderr"))
    if error:
        lines.extend(["Root error reported by Manim:", error])

    rendered: set[str] = set()
    for key in _DIAGNOSTIC_ORDER:
        if key not in diagnostics:
            continue
        rendered.add(key)
        lines.append(f"{key}: {_scalar(diagnostics[key])}")

    for key in _STREAM_KEYS:
        if key not in diagnostics:
            continue
        rendered.add(key)
        lines.extend([f"{key} (tail):", _tail(diagnostics[key])])

    quality_lines = _quality_lines(diagnostics)
    if quality_lines:
        lines.extend(["Deterministic visual findings:", *quality_lines])

    remaining = [key for key in sorted(diagnostics) if key not in rendered]
    for key in remaining:
        lines.append(f"{key}: {_scalar(diagnostics[key])}")
    return lines


def _corrective_instruction(
    diagnostics: Mapping[str, object],
    failing: list[str],
) -> str:
    """Close the prompt with the single action the model must take."""

    error = _root_error(diagnostics.get("stderr"))
    tail = "Then return the complete corrected Python source without commentary."
    if error:
        target = "that exact line" if failing else "the failure above"
        return f"Fix {target} so Manim no longer raises `{error}`. {tail}"

    # The render succeeded and the video was still rejected. Saying "make it
    # render" here contradicts the diagnosis and the model repeats itself.
    reasons = _reasons(diagnostics)
    quality = _quality_reasons(diagnostics)
    reasons.extend(item for item in quality if item not in reasons)
    if reasons:
        joined = "; ".join(reasons)
        return (
            "The code already renders. Change what the animation draws so every "
            f"one of these stops being true: {joined}. {tail}"
        )
    return f"Fix the rejection above. {tail}"


def _quality_lines(diagnostics: Mapping[str, object]) -> list[str]:
    """Render structured visual findings with their measured evidence."""

    values: list[object] = []
    for key in _QUALITY_KEYS:
        value = diagnostics.get(key)
        if isinstance(value, list):
            values.extend(value)
        elif isinstance(value, Mapping):
            findings = value.get("findings")
            if isinstance(findings, list):
                values.extend(findings)
    lines: list[str] = []
    for value in values:
        if not isinstance(value, Mapping):
            continue
        code: object = value.get("code", "VISUAL_FINDING")
        severity: object = value.get("severity", "failure")
        observed: object = value.get("observed", {})
        expected: object = value.get("expected", {})
        suggestion: object = value.get("suggestion", "")
        lines.append(
            f"{severity} {code}: observed={_scalar(observed)}; "
            f"expected={_scalar(expected)}; correction={suggestion}"
        )
    return lines


def _quality_reasons(diagnostics: Mapping[str, object]) -> list[str]:
    """Return actionable correction text for structured findings."""

    values: list[object] = []
    for key in _QUALITY_KEYS:
        value = diagnostics.get(key)
        if isinstance(value, list):
            values.extend(value)
        elif isinstance(value, Mapping):
            findings = value.get("findings")
            if isinstance(findings, list):
                values.extend(findings)
    reasons: list[str] = []
    for value in values:
        if not isinstance(value, Mapping):
            continue
        code = str(value.get("code", "VISUAL_FINDING"))
        suggestion = str(value.get("suggestion", "")).strip()
        observed = _scalar(value.get("observed", {}))
        expected = _scalar(value.get("expected", {}))
        detail = f"{code} (observed {observed}; expected {expected})"
        if suggestion:
            detail += f": {suggestion}"
        reasons.append(detail)
    return reasons


def _headline(diagnostics: Mapping[str, object]) -> str:
    """Open the diagnosis with what actually happened."""

    if _rendered_successfully(diagnostics):
        return (
            "The previous attempt RENDERED SUCCESSFULLY but the resulting video "
            "was REJECTED: it does not show what the scene specification asks "
            "for. The code runs; the picture is wrong."
        )
    return "The previous render FAILED. Diagnose the exact cause below and fix it."


def _rendered_successfully(diagnostics: Mapping[str, object]) -> bool:
    return diagnostics.get("exit_code") == 0 and not _root_error(
        diagnostics.get("stderr")
    )


def _reasons(diagnostics: Mapping[str, object]) -> list[str]:
    """Return every recorded validator reason, most prescriptive first.

    One reason describes the picture, another names the code change that
    produced it.  The model needs both, and the one naming a code change is
    the one it can act on directly.
    """

    raw = diagnostics.get("validator_reasons")
    if not isinstance(raw, (list, tuple)):
        return []
    texts = [str(reason).strip() for reason in raw if str(reason).strip()]
    prescriptive = [text for text in texts if _NAMES_A_CODE_CHANGE in text]
    descriptive = [text for text in texts if _NAMES_A_CODE_CHANGE not in text]
    return [*prescriptive, *descriptive]


def _failing_source_lines(stderr: object, previous_code: object) -> list[str]:
    """Return the generated-source lines Manim's traceback points at.

    Manim renders a Rich traceback whose frames interleave library code with
    the generated scene.  A line belongs to the generated file exactly when it
    also appears in the candidate that produced the failure, so the candidate
    itself is the filter and no path parsing is needed.
    """

    if not isinstance(stderr, str) or not isinstance(previous_code, str):
        return []
    own = {line.strip() for line in previous_code.splitlines() if line.strip()}
    if not own:
        return []
    found: list[str] = []
    for raw in stderr.splitlines():
        text = raw.strip().strip("│").strip()
        if not text.startswith(_TRACEBACK_MARKER):
            continue
        body = text.lstrip(_TRACEBACK_MARKER).strip().split(None, 1)
        if len(body) != 2:
            continue
        number, remainder = body
        # Rich also draws indentation guides with the same box character.
        code = remainder.replace("│", " ").strip()
        if code and code in own:
            entry = f"  line {number}: {code}"
            if entry not in found:
                found.append(entry)
    return found


def _root_error(stderr: object) -> str:
    """Return the last exception-shaped line of a Manim traceback."""

    if not isinstance(stderr, str):
        return ""
    for line in reversed(stderr.splitlines()):
        stripped = line.strip().strip("│").strip()
        # Rich draws the traceback inside a box; only the trailing unboxed
        # exception line names the actual failure.
        if stripped and "Error" in stripped and not stripped.startswith("❱"):
            return stripped
    return ""


def _tail(value: object) -> str:
    """Bound one process stream to its decisive trailing region."""

    if not isinstance(value, str):
        return _scalar(value)
    tail = "\n".join(value.splitlines()[-_STREAM_TAIL_LINES:])
    if len(tail) > _STREAM_TAIL_CHARS:
        tail = tail[-_STREAM_TAIL_CHARS:]
    return tail


def _scalar(value: object) -> str:
    """Render one diagnostic value without hiding its content."""

    if isinstance(value, (list, tuple)):
        return ", ".join(_scalar(item) for item in value)
    if isinstance(value, Mapping):
        return "; ".join(f"{key}={_scalar(item)}" for key, item in sorted(value.items()))
    return str(value)


__all__ = ["build_correction_prompt", "build_generation_prompt", "build_prompt"]
