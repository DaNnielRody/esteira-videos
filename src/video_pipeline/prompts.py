"""Deterministic prompt text used by the render-in-the-loop pipeline.

This module is the single source of truth for what is sent to the provider.
The pipeline preserves the very same text as ``prompt.txt`` for each attempt,
so the stored artifact reproduces the request byte for byte.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING

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


def build_prompt(request: ProviderRequest) -> str:
    """Build the generation or correction prompt for one provider request."""

    lines = [
        _BASE_INSTRUCTIONS,
        "Scene specification:",
        f"schema_version: {request.schema_version}",
        f"scene_name: {request.scene_name}",
        f"description: {request.description}",
    ]
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
        lines.extend(
            [
                "This is the code that failed. Rewrite it; never return it unchanged:",
                "```python",
                request.previous_code,
                "```",
            ]
        )
    lines.append(_corrective_instruction(request.diagnostics, failing))
    return "\n".join(lines)


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

    lines = [
        "The previous render FAILED. Diagnose the exact cause below and fix it.",
    ]
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
    parts = ["Fix"]
    parts.append("that exact line" if failing else "the failure above")
    if error:
        parts.append(f"so Manim no longer raises `{error}`.")
    else:
        parts.append("so Manim renders the scene.")
    parts.append(
        "Then return the complete corrected Python source without commentary."
    )
    return " ".join(parts)


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
