"""Direction regressions over observed checkpoints, never over plan claims alone.

These checks prove sampled logical geometry and event order. They do not claim
pixel coverage between checkpoints or judge pedagogical quality.
"""

from __future__ import annotations

from video_pipeline.quality import QualityFinding
from video_pipeline.runtime import ObservedScene
from video_pipeline.scene_plan import ScenePlan


def check_direction(plan: ScenePlan, observed: ObservedScene) -> list[QualityFinding]:
    """Evaluate declared direction promises, failing closed on missing evidence."""
    if observed.scene_id != plan.id:
        raise ValueError("observed scene ID does not match scene plan")
    contract = plan.direction
    if contract is None:
        return []
    findings: list[QualityFinding] = []
    # Several instantaneous mutations can share a time (including the empty
    # pre-construct snapshot). The last snapshot is the settled state rendered
    # at that instant; zero-time intermediate states have no display duration.
    checkpoints = list({round(s.instant_seconds, 6): s for s in observed.checkpoints}.values())

    def fail(code: str, ids: list[str], time: float, explanation: str) -> None:
        findings.append(
            QualityFinding(
                code=f"DIRECTION_{code}",
                severity="failure",
                scene_id=plan.id,
                object_ids=ids,
                instant_seconds=time,
                explanation=explanation,
                suggestion="Correct the visual unit or supply checkpoints at contract boundaries.",
            )
        )

    for token in contract.tokens:
        ids = [token.text_id, token.container_id]
        samples = [
            s
            for s in checkpoints
            if token.start_seconds - 1e-6 <= s.instant_seconds <= plan.duration_seconds + 1e-6
        ]
        if not all(
            any(abs(s.instant_seconds - t) < 1e-6 for s in samples)
            for t in (token.start_seconds, token.end_seconds)
        ):
            fail(
                "EVIDENCE_MISSING",
                ids,
                token.start_seconds,
                "Token requires checkpoints at its live start and completed EXIT.",
            )
        for sample in samples:
            visible = {o.id: o for o in sample.objects if o.visible}
            text, box = visible.get(token.text_id), visible.get(token.container_id)
            if sample.instant_seconds >= token.end_seconds - 1e-6:
                if text is not None or box is not None:
                    fail(
                        "TOKEN_ORPHAN",
                        ids,
                        sample.instant_seconds,
                        "Token members remain after the declared EXIT; retire both IDs.",
                    )
            elif text is None or box is None:
                fail(
                    "TOKEN_ORPHAN",
                    ids,
                    sample.instant_seconds,
                    "The live token must contain both text and container.",
                )
            else:
                gaps = (
                    text.bbox.left - box.bbox.left,
                    box.bbox.right - text.bbox.right,
                    text.bbox.top - box.bbox.top,
                    box.bbox.bottom - text.bbox.bottom,
                )
                if min(gaps) + 1e-9 < token.padding:
                    fail(
                        "TOKEN_PADDING",
                        ids,
                        sample.instant_seconds,
                        "Text exceeds its container or declared normalized padding.",
                    )
    for aspect in contract.aspects:
        ids = [aspect.object_id]
        samples = [
            s
            for s in checkpoints
            if aspect.start_seconds - 1e-6 <= s.instant_seconds <= aspect.end_seconds + 1e-6
        ]
        if not all(
            any(abs(s.instant_seconds - t) < 1e-6 for s in samples)
            for t in (aspect.start_seconds, aspect.end_seconds)
        ):
            fail(
                "EVIDENCE_MISSING",
                ids,
                aspect.start_seconds,
                "Aspect ratio requires checkpoints at both interval boundaries.",
            )
        for sample in samples:
            item = next((o for o in sample.objects if o.id == aspect.object_id and o.visible), None)
            if item is None or item.bbox.height <= 0 or item.bbox.width <= 0:
                fail(
                    "EVIDENCE_MISSING",
                    ids,
                    sample.instant_seconds,
                    "Aspect ratio requires visible, nondegenerate geometry.",
                )
                continue
            ratio = (
                item.bbox.width
                * sample.camera.frame_width
                / (item.bbox.height * sample.camera.frame_height)
            )
            if abs(ratio / aspect.expected_ratio - 1) > aspect.relative_tolerance + 1e-9:
                fail(
                    "ASPECT_RATIO",
                    ids,
                    sample.instant_seconds,
                    f"Axis-aligned ratio {ratio:.6g} differs from {aspect.expected_ratio:.6g} "
                    f"beyond relative tolerance {aspect.relative_tolerance:.6g}.",
                )
    for process in contract.processing:
        ids = [process.model_id, process.output_id]
        matches = [
            a
            for a in observed.animations
            if a.name == process.animation
            and process.model_id in a.object_ids
            and abs(a.start_seconds - process.start_seconds) < 1e-6
            and abs(a.end_seconds - process.end_seconds) < 1e-6
        ]
        samples = [
            s for s in checkpoints if 0 <= s.instant_seconds <= process.output_by_seconds + 1e-6
        ]
        required_times = (
            0.0,
            process.start_seconds,
            process.end_seconds,
            process.output_by_seconds,
        )
        if not matches or not all(
            any(abs(s.instant_seconds - t) < 1e-6 for s in samples) for t in required_times
        ):
            fail(
                "EVIDENCE_MISSING",
                ids,
                process.start_seconds,
                "Require the matching model animation and checkpoints at scene start, "
                "processing boundaries and output deadline.",
            )
        output_seen = False
        for sample in samples:
            visible_ids = {o.id for o in sample.objects if o.visible}
            if process.start_seconds - 1e-6 <= sample.instant_seconds <= process.end_seconds + 1e-6:
                if process.model_id not in visible_ids:
                    fail(
                        "EVIDENCE_MISSING",
                        ids,
                        sample.instant_seconds,
                        "Processing must act on a visible model.",
                    )
            if process.output_id in visible_ids:
                if sample.instant_seconds < process.end_seconds - 1e-6:
                    fail(
                        "OUTPUT_EARLY",
                        ids,
                        sample.instant_seconds,
                        "Fresh output is visible before processing finishes.",
                    )
                else:
                    output_seen = True
        if not output_seen:
            fail(
                "EVIDENCE_MISSING",
                ids,
                process.output_by_seconds,
                "No visible output was observed between processing completion and deadline.",
            )
    return findings
