"""Deterministic visual critics for plans and observed Manim facts."""

from __future__ import annotations

import math
import re

from video_pipeline.expectations import check_expectations
from video_pipeline.observation import ObservedShape
from video_pipeline.quality import QualityFinding, QualityReport
from video_pipeline.runtime import BoundingBox, ObservedObject, ObservedScene
from video_pipeline.scene_plan import Beat, ScenePlan, VisualObject
from video_pipeline.theme import DEFAULT_VIDEO_THEME, SemanticRegion, VideoTheme

_HEX_COLOR = re.compile(r"^#[0-9A-Fa-f]{6}$")
_NAMED_COLORS = {
    "black": "#000000",
    "white": "#FFFFFF",
    "red": "#FF0000",
    "green": "#00FF00",
    "blue": "#0000FF",
    "yellow": "#FFFF00",
    "orange": "#FFA500",
    "purple": "#800080",
    "pink": "#FFC0CB",
    "grey": "#808080",
    "gray": "#808080",
}


def evaluate_visual_quality(
    plan: ScenePlan,
    observed: ObservedScene,
    *,
    attempt: int = 1,
    theme: VideoTheme | None = None,
) -> QualityReport:
    """Run every deterministic critic and return one structured report."""

    if observed.scene_id != plan.id:
        raise ValueError("observed scene ID does not match scene plan")
    active_theme = theme or plan.theme or DEFAULT_VIDEO_THEME
    findings: list[QualityFinding] = []
    _safe_area_findings(plan, observed, active_theme, findings)
    _overlap_findings(plan, observed, findings)
    _contrast_findings(plan, observed, active_theme, findings)
    _legibility_findings(plan, observed, active_theme, findings)
    _rhythm_findings(plan, observed, active_theme, findings)
    _coherence_findings(plan, observed, findings)
    for finding in findings:
        object.__setattr__(finding, "scene_id", plan.id)
    return QualityReport(scene_id=plan.id, attempt=attempt, findings=_deduplicate(findings))


def check_safe_area(
    plan: ScenePlan, observed: ObservedScene, *, theme: VideoTheme | None = None
) -> list[QualityFinding]:
    """Return clipping, invisibility, and safe-area findings only."""

    findings: list[QualityFinding] = []
    _safe_area_findings(plan, observed, theme or plan.theme, findings)
    return _deduplicate(findings, scene_id=plan.id)


def check_overlaps(plan: ScenePlan, observed: ObservedScene) -> list[QualityFinding]:
    """Return relevant prohibited overlap findings only."""

    findings: list[QualityFinding] = []
    _overlap_findings(plan, observed, findings)
    return _deduplicate(findings, scene_id=plan.id)


def check_contrast(
    plan: ScenePlan, observed: ObservedScene, *, theme: VideoTheme | None = None
) -> list[QualityFinding]:
    """Return measured contrast and semantic-colour findings only."""

    findings: list[QualityFinding] = []
    _contrast_findings(plan, observed, theme or plan.theme, findings)
    return _deduplicate(findings, scene_id=plan.id)


def check_legibility(
    plan: ScenePlan, observed: ObservedScene, *, theme: VideoTheme | None = None
) -> list[QualityFinding]:
    """Return measurable typography and reading-time findings only."""

    findings: list[QualityFinding] = []
    _legibility_findings(plan, observed, theme or plan.theme, findings)
    return _deduplicate(findings, scene_id=plan.id)


def check_rhythm(
    plan: ScenePlan, observed: ObservedScene, *, theme: VideoTheme | None = None
) -> list[QualityFinding]:
    """Return measurable timing and visual-change findings only."""

    findings: list[QualityFinding] = []
    _rhythm_findings(plan, observed, theme or plan.theme, findings)
    return _deduplicate(findings, scene_id=plan.id)


def check_plan_coherence(plan: ScenePlan, observed: ObservedScene) -> list[QualityFinding]:
    """Return findings where logical observations disagree with the plan."""

    findings: list[QualityFinding] = []
    _coherence_findings(plan, observed, findings)
    return _deduplicate(findings, scene_id=plan.id)


def relative_luminance(color: str) -> float:
    """Calculate WCAG relative luminance for one observed RGB colour."""

    rgb = _rgb(color)
    if rgb is None:
        raise ValueError("contrast colours must be six-digit hex values")
    linear = []
    for channel in rgb:
        value = channel / 255.0
        linear.append(value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4)
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def contrast_ratio(foreground: str, background: str) -> float:
    """Return the WCAG contrast ratio between two colours."""

    first = relative_luminance(foreground)
    second = relative_luminance(background)
    lighter, darker = max(first, second), min(first, second)
    return (lighter + 0.05) / (darker + 0.05)


def _safe_area_findings(
    plan: ScenePlan,
    observed: ObservedScene,
    theme: VideoTheme,
    findings: list[QualityFinding],
) -> None:
    area = theme.safe_area
    for item in _unique_objects(observed):
        box = item.bbox
        outside_frame = box.right <= 0 or box.left >= 1 or box.bottom <= 0 or box.top >= 1
        clipped = box.left < 0 or box.top < 0 or box.right > 1 or box.bottom > 1
        if not item.visible or item.width <= 0 or item.height <= 0 or outside_frame:
            findings.append(
                _finding(
                    "OBJECT_INVISIBLE",
                    item,
                    observed={"visible": item.visible, "width": item.width, "height": item.height},
                    expected={"visible": True, "min_width": 0.001, "min_height": 0.001},
                    explanation="The logical object has no visible area in the frame.",
                    suggestion=(
                        "Keep the object inside the camera frame and give it a positive size."
                    ),
                )
            )
        elif clipped:
            findings.append(
                _finding(
                    "OBJECT_CLIPPED",
                    item,
                    observed=_box_document(box),
                    expected={"left": 0.0, "top": 0.0, "right": 1.0, "bottom": 1.0},
                    explanation="The object is partially outside the camera frame.",
                    suggestion=(
                        "Scale or reposition the object until its full bounds fit the frame."
                    ),
                )
            )
        textual = _is_textual(item)
        if textual and (
            box.left < area.left
            or box.right > area.right
            or box.top < area.top
            or box.bottom > area.bottom
        ):
            code = "FORMULA_OUTSIDE_SAFE_AREA" if _is_formula(item) else "TEXT_OUTSIDE_SAFE_AREA"
            findings.append(
                _finding(
                    code,
                    item,
                    observed=_box_document(box),
                    expected={
                        "min_left": area.left,
                        "min_top": area.top,
                        "max_right": area.right,
                        "max_bottom": area.bottom,
                    },
                    explanation="Textual content extends beyond the declared reading safe area.",
                    suggestion=(
                        "Reduce the content width or split it into two lines inside the safe area."
                    ),
                )
            )
        if item.width > 1.0 or item.height > 1.0:
            findings.append(
                _finding(
                    "OBJECT_DIMENSION_INVALID",
                    item,
                    observed={"width": item.width, "height": item.height},
                    expected={"max_width": 1.0, "max_height": 1.0},
                    explanation="The measured object is larger than the camera frame.",
                    suggestion="Reduce the object's scale before rendering it.",
                )
            )


def _overlap_findings(
    plan: ScenePlan,
    observed: ObservedScene,
    findings: list[QualityFinding],
) -> None:
    plan_objects = {item.id: item for item in plan.objects}
    for state in _states(observed):
        visible = [item for item in state if item.visible and item.width > 0 and item.height > 0]
        for index, left in enumerate(visible):
            for right in visible[index + 1 :]:
                left_plan = plan_objects.get(left.id)
                right_plan = plan_objects.get(right.id)
                instant = max(left.logical_time, right.logical_time)
                if _overlap_allowed(
                    left_plan,
                    right_plan,
                    observed,
                    object_ids={left.id, right.id},
                    instant=instant,
                    plan=plan,
                ):
                    continue
                intersection = _intersection(left.bbox, right.bbox)
                if intersection <= 0:
                    continue
                smaller = min(left.width * left.height, right.width * right.height)
                overlap_fraction = intersection / smaller if smaller else 0.0
                if overlap_fraction < 0.25:
                    continue
                iou = _iou(left.bbox, right.bbox)
                findings.append(
                    QualityFinding(
                        code="PROHIBITED_OVERLAP",
                        severity="failure",
                        scene_id=plan.id,
                        instant_seconds=min(left.logical_time, right.logical_time),
                        object_ids=sorted([left.id, right.id]),
                        observed={"intersection_fraction": overlap_fraction, "iou": iou},
                        expected={"max_intersection_fraction": 0.25},
                        explanation=(
                            "Two objects with a prohibited overlap occupy the same reading space."
                        ),
                        suggestion=(
                            "Separate the objects, declare an intentional overlap, or group them."
                        ),
                    )
                )


def _contrast_findings(
    plan: ScenePlan,
    observed: ObservedScene,
    theme: VideoTheme,
    findings: list[QualityFinding],
) -> None:
    expected_background = theme.background_color
    plan_objects = {item.id: item for item in plan.objects}
    for item in _unique_objects(observed):
        if not item.visible or item.kind.lower() == "background":
            continue
        actual, ambiguous, evidence = _pixel_color(item, observed)
        if observed.frames and ambiguous:
            findings.append(
                _finding(
                    "PIXEL_COLOR_AMBIGUOUS",
                    item,
                    observed={"candidates": evidence},
                    expected={"one_matching_shape": True},
                    explanation="More than one frame shape could represent this semantic object.",
                    suggestion=(
                        "Register a unique object position or provide an unambiguous "
                        "frame checkpoint."
                    ),
                    severity="warning",
                )
            )
            continue
        if actual is None:
            if observed.frames:
                findings.append(
                    _finding(
                        "PIXEL_COLOR_UNMATCHED",
                        item,
                        observed={
                            "center_x": item.center_x,
                            "center_y": item.center_y,
                            "kind": item.kind,
                        },
                        expected={"pixel_evidence": True},
                        explanation=(
                            "No HSV-classified frame shape could be matched to this object."
                        ),
                        suggestion=(
                            "Keep the object visible in a sampled frame and preserve "
                            "its semantic position."
                        ),
                        severity="warning",
                    )
                )
                continue
            findings.append(
                _finding(
                    "OBSERVED_COLOR_UNREADABLE",
                    item,
                    observed={"color": item.observed_color},
                    expected={"format": "#RRGGBB"},
                    explanation=(
                        "The runtime could not express the observed colour in a measurable format."
                    ),
                    suggestion="Record the rendered RGB colour from the mobject or frame evidence.",
                    severity="warning",
                )
            )
            continue
        ratio = contrast_ratio(actual, expected_background)
        threshold = 4.5 if _is_textual(item) else 3.0
        if ratio < threshold:
            findings.append(
                _finding(
                    "LOW_CONTRAST",
                    item,
                    observed={
                        "foreground": actual,
                        "background": expected_background,
                        "ratio": ratio,
                    },
                    expected={"minimum_ratio": threshold},
                    explanation=(
                        "The rendered foreground does not contrast enough with the "
                        "theme background."
                    ),
                    suggestion=(
                        "Use the theme text, primary, or accent role with a higher contrast value."
                    ),
                )
            )
        declared = plan_objects.get(item.id)
        if declared is not None and declared.color_role is not None:
            expected = theme.color(declared.color_role)
            if _colour_distance(actual, expected) > 0.18:
                findings.append(
                    _finding(
                        "SEMANTIC_COLOR_MISMATCH",
                        item,
                        observed={"color": actual, "color_role": item.color_role},
                        expected={"color_role": declared.color_role, "color": expected},
                        explanation=(
                            "The observed rendered colour does not match the semantic "
                            "role in the plan."
                        ),
                        suggestion=(
                            f"Use the theme role {declared.color_role!r} instead of a "
                            "scene-local colour."
                        ),
                    )
                )


def _legibility_findings(
    plan: ScenePlan,
    observed: ObservedScene,
    theme: VideoTheme,
    findings: list[QualityFinding],
) -> None:
    for item in _unique_objects(observed):
        if not item.visible or not _is_textual(item):
            continue
        pixel_height = item.height * theme.resolution[1]
        minimum_pixels = 16.0 if _is_formula(item) else 18.0
        if pixel_height < minimum_pixels:
            findings.append(
                _finding(
                    "TEXT_TOO_SMALL",
                    item,
                    observed={"pixel_height": pixel_height},
                    expected={"minimum_pixel_height": minimum_pixels},
                    explanation="The rendered text height is below the legibility threshold.",
                    suggestion=(
                        "Increase the theme font size or give the label more vertical space."
                    ),
                )
            )
        if _is_formula(item) and item.width > theme.safe_area.right - theme.safe_area.left:
            findings.append(
                _finding(
                    "FORMULA_TOO_WIDE",
                    item,
                    observed={"width_fraction": item.width},
                    expected={"max_width_fraction": theme.safe_area.right - theme.safe_area.left},
                    explanation="The formula is wider than the usable safe area.",
                    suggestion=(
                        "Use a smaller formula size or break the expression into multiple lines."
                    ),
                )
            )
        content = item.text or item.formula or ""
        lines = content.count("\n") + 1
        if lines > plan.layout.max_text_lines:
            findings.append(
                _finding(
                    "TEXT_LINES_EXCESSIVE",
                    item,
                    observed={"lines": lines},
                    expected={"max_lines": plan.layout.max_text_lines},
                    explanation="The content has more lines than the layout contract allows.",
                    suggestion="Shorten the copy or split it into separate beats.",
                )
            )
        present_seconds = _observed_present_seconds(observed, item.id)
        if present_seconds is None:
            present_seconds = _present_seconds(plan, item.id)
        required_seconds = _required_read_seconds(plan, item.id, theme)
        if present_seconds is not None and present_seconds < required_seconds:
            findings.append(
                _finding(
                    "READ_DURATION_TOO_SHORT",
                    item,
                    observed={"duration_seconds": present_seconds},
                    expected={"minimum_seconds": required_seconds},
                    explanation=(
                        "The textual content is present for less time than its reading "
                        "contract requires."
                    ),
                    suggestion=(
                        "Extend the beat or shorten the text before introducing the "
                        "next visual action."
                    ),
                )
            )
        if item.width + item.height > plan.layout.max_content_density:
            findings.append(
                _finding(
                    "CONTENT_DENSITY_HIGH",
                    item,
                    observed={"width_plus_height": item.width + item.height},
                    expected={"max_content_density": plan.layout.max_content_density},
                    explanation=(
                        "The measured textual footprint is too dense for the declared layout."
                    ),
                    suggestion="Reduce copy, increase spacing, or distribute content across beats.",
                    severity="warning",
                )
            )

    minimum_spacing = plan.layout.minimum_spacing or theme.min_spacing
    plan_objects = {item.id: item for item in plan.objects}
    for state in _states(observed):
        visible = [item for item in state if item.visible and item.width > 0 and item.height > 0]
        for index, left in enumerate(visible):
            if not _is_textual(left):
                continue
            for right in visible[index + 1 :]:
                if not _is_textual(right) or _intersection(left.bbox, right.bbox) > 0:
                    continue
                edge_gap = _edge_gap(left.bbox, right.bbox)
                if edge_gap >= minimum_spacing:
                    continue
                if _overlap_allowed(
                    plan_objects.get(left.id),
                    plan_objects.get(right.id),
                    observed,
                    object_ids={left.id, right.id},
                    instant=max(left.logical_time, right.logical_time),
                    plan=plan,
                ):
                    continue
                findings.append(
                    QualityFinding(
                        code="INSUFFICIENT_SPACING",
                        severity="warning",
                        scene_id=plan.id,
                        instant_seconds=max(left.logical_time, right.logical_time),
                        object_ids=sorted([left.id, right.id]),
                        observed={"edge_gap": edge_gap},
                        expected={"minimum_spacing": minimum_spacing},
                        explanation=(
                            "Adjacent textual objects have less than the declared reading spacing."
                        ),
                        suggestion="Increase the gap or distribute the text across separate beats.",
                    )
                )


def _rhythm_findings(
    plan: ScenePlan,
    observed: ObservedScene,
    theme: VideoTheme,
    findings: list[QualityFinding],
) -> None:
    plan_objects = {item.id: item for item in plan.objects}
    for beat in plan.beats:
        duration = beat.effective_duration
        if duration <= 0:
            continue
        contains_text = any(
            _is_plan_textual(plan_objects.get(object_id)) for object_id in beat.objects
        )
        minimum = beat.min_read_seconds or (
            theme.min_read_duration if contains_text else _animation_duration(beat, theme)
        )
        if duration < minimum:
            findings.append(
                QualityFinding(
                    code="BEAT_TOO_FAST",
                    severity="failure",
                    scene_id=plan.id,
                    beat_id=beat.id,
                    instant_seconds=beat.start_seconds,
                    object_ids=list(beat.objects),
                    observed={"duration_seconds": duration},
                    expected={"minimum_seconds": minimum},
                    explanation=(
                        "The beat has less time than the declared animation or reading threshold."
                    ),
                    suggestion=(
                        "Increase this beat's duration or move the content to a separate beat."
                    ),
                )
            )

    checkpoints = sorted(observed.checkpoints, key=lambda checkpoint: checkpoint.instant_seconds)
    for previous, current in zip(checkpoints, checkpoints[1:], strict=False):
        interval = current.instant_seconds - previous.instant_seconds
        if interval >= max(3.0, theme.min_read_duration * 3) and not current.visual_change:
            findings.append(
                QualityFinding(
                    code="LONG_STATIC_INTERVAL",
                    severity="warning",
                    scene_id=plan.id,
                    beat_id=current.beat_id,
                    instant_seconds=previous.instant_seconds,
                    object_ids=[item.id for item in current.objects],
                    observed={"duration_seconds": interval, "visual_change": current.visual_change},
                    expected={"max_static_seconds": max(3.0, theme.min_read_duration * 3)},
                    explanation=(
                        "The observed timeline holds a frame for an unusually long interval."
                    ),
                    suggestion=(
                        "Add a visual change, shorten the wait, or declare the hold intentionally."
                    ),
                )
            )

    for animation in observed.animations:
        if len(animation.object_ids) >= 3:
            findings.append(
                QualityFinding(
                    code="SIMULTANEOUS_IMPORTANT_ACTIONS",
                    severity="warning",
                    scene_id=plan.id,
                    instant_seconds=animation.start_seconds,
                    object_ids=list(animation.object_ids),
                    observed={"object_count": len(animation.object_ids)},
                    expected={"max_simultaneous_objects": 2},
                    explanation="Several important objects change in one simultaneous animation.",
                    suggestion="Stagger the actions so the viewer can identify each change.",
                )
            )

    timeline = _timeline_states(observed)
    first_seen: dict[str, float] = {}
    for instant, _checkpoint_beat_id, objects in timeline:
        for object_id, item in objects.items():
            if item.visible and item.width > 0 and item.height > 0:
                first_seen.setdefault(object_id, instant)
    introduction_times = sorted(first_seen.items(), key=lambda value: (value[1], value[0]))
    for index, (_object_id, instant) in enumerate(introduction_times):
        burst = [
            candidate_id
            for candidate_id, candidate_time in introduction_times[index:]
            if candidate_time - instant <= 1.0
        ]
        if len(burst) < 3:
            continue
        findings.append(
            QualityFinding(
                code="OBJECT_BURST",
                severity="warning",
                scene_id=plan.id,
                instant_seconds=instant,
                object_ids=burst,
                observed={
                    "new_object_count": len(burst),
                    "window_seconds": max(first_seen[candidate_id] for candidate_id in burst)
                    - instant,
                },
                expected={"max_new_objects": 2, "window_seconds": 1.0},
                explanation="Several new visual objects appear within one second.",
                suggestion=(
                    "Stagger introductions across beats so each new object can be identified."
                ),
            )
        )
        break

    observed_duration = _observed_duration(observed)
    if observed_duration is not None and abs(observed_duration - plan.duration_seconds) > 0.75:
        findings.append(
            QualityFinding(
                code="OBSERVED_DURATION_MISMATCH",
                severity="failure",
                scene_id=plan.id,
                instant_seconds=observed_duration,
                observed={"duration_seconds": observed_duration},
                expected={"duration_seconds": plan.duration_seconds, "tolerance_seconds": 0.75},
                explanation="The logical timeline duration differs materially from the plan.",
                suggestion=(
                    "Adjust waits and animation run_time values to match the planned duration."
                ),
            )
        )
    if (
        plan.beats
        and observed_duration is not None
        and not any(
            item.visible and item.width > 0 and item.height > 0 for item in observed.final_state
        )
    ):
        findings.append(
            QualityFinding(
                code="MISSING_VISUAL_CLOSURE",
                severity="warning",
                scene_id=plan.id,
                instant_seconds=observed_duration,
                observed={"final_object_count": 0},
                expected={"final_object_count": 1},
                explanation="The scene ends without a visible closing state.",
                suggestion=(
                    "Leave the final concept visible or declare an intentional fade-out transition."
                ),
            )
        )


def _coherence_findings(
    plan: ScenePlan,
    observed: ObservedScene,
    findings: list[QualityFinding],
) -> None:
    plan_objects = {item.id: item for item in plan.objects}
    observed_objects = _unique_objects(observed)
    observed_ids = {item.id for item in observed_objects}
    for item in plan.objects:
        if item.required and item.id not in observed_ids:
            findings.append(
                QualityFinding(
                    code="REQUIRED_OBJECT_MISSING",
                    severity="failure",
                    scene_id=plan.id,
                    object_ids=[item.id],
                    observed={"present": False},
                    expected={"present": True, "kind": item.kind},
                    explanation="A required object from the visual plan was not observed.",
                    suggestion="Create and register the required object with its semantic ID.",
                )
            )
    for item in observed_objects:
        if item.id not in plan_objects and item.kind.lower() != "background":
            findings.append(
                QualityFinding(
                    code="UNPLANNED_OBJECT",
                    severity="warning",
                    scene_id=plan.id,
                    object_ids=[item.id],
                    observed={"id": item.id, "kind": item.kind},
                    expected={"declared": False},
                    explanation="A visible object is present without a matching plan declaration.",
                    suggestion="Add it to the plan or remove the unintended decoration.",
                )
            )

    for item in observed_objects:
        declared = plan_objects.get(item.id)
        if declared is None:
            continue
        if declared.kind.lower() != item.kind.lower():
            findings.append(
                _finding(
                    "OBJECT_KIND_MISMATCH",
                    item,
                    observed={"kind": item.kind},
                    expected={"kind": declared.kind},
                    explanation="The observed object kind differs from the visual plan.",
                    suggestion=f"Use the planned {declared.kind!r} mobject for this semantic ID.",
                )
            )
        expected_region = declared.region or plan.layout.object_regions.get(item.id)
        if expected_region is not None and _region_bounds(plan, expected_region) is not None:
            if not _in_region(item, _region_bounds(plan, expected_region)):
                findings.append(
                    _finding(
                        "OBJECT_REGION_MISMATCH",
                        item,
                        observed={"center_x": item.center_x, "center_y": item.center_y},
                        expected={"region": expected_region},
                        explanation=(
                            "The observed object is in a different semantic region than planned."
                        ),
                        suggestion=f"Move the object into the declared {expected_region!r} region.",
                    )
                )

    timeline = _timeline_states(observed)
    cursor = 0
    previous_match: tuple[float, dict[str, ObservedObject]] | None = None
    for beat in plan.beats:
        match_index = _find_beat_state(timeline, beat, start=cursor)
        if match_index is None:
            earlier_match = _find_beat_state(timeline, beat, start=0, stop=cursor)
            code = "BEAT_SEQUENCE_WRONG" if earlier_match is not None else "BEAT_NOT_OBSERVED"
            explanation = (
                "The beat objects were observed, but not in the authored sequence."
                if code == "BEAT_SEQUENCE_WRONG"
                else "No observed state contains the objects assigned to this beat."
            )
            suggestion = (
                "Animate and checkpoint this beat after the preceding beat."
                if code == "BEAT_SEQUENCE_WRONG"
                else "Register the beat objects and checkpoint the intended state."
            )
            findings.append(
                QualityFinding(
                    code=code,
                    severity="failure",
                    scene_id=plan.id,
                    beat_id=beat.id,
                    object_ids=list(beat.objects),
                    observed={"objects": []},
                    expected={"objects": list(beat.objects), "action": beat.action},
                    explanation=explanation,
                    suggestion=suggestion,
                )
            )
            continue
        cursor = match_index + 1
        instant, _checkpoint_beat_id, matched_objects = timeline[match_index]
        if beat.movement is not None:
            previous_objects = (
                previous_match[1]
                if previous_match is not None
                else timeline[match_index - 1][2]
                if match_index > 0
                else {}
            )
            if not any(
                object_id in previous_objects
                and _movement_matches(
                    previous_objects[object_id], matched_objects[object_id], beat.movement
                )
                for object_id in beat.objects
                if object_id in matched_objects
            ):
                findings.append(
                    QualityFinding(
                        code="BEAT_MOVEMENT_MISSING",
                        severity="failure",
                        scene_id=plan.id,
                        beat_id=beat.id,
                        instant_seconds=instant,
                        object_ids=list(beat.objects),
                        observed={"movement": None},
                        expected={"movement": beat.movement},
                        explanation=(
                            "The beat declares movement that was not observed between checkpoints."
                        ),
                        suggestion=(
                            "Move the same registered object in the declared direction "
                            "and checkpoint it."
                        ),
                    )
                )
        previous_match = (instant, matched_objects)
        candidates = [
            matched_objects[object_id] for object_id in beat.objects if object_id in matched_objects
        ]
        for item in candidates:
            if beat.region is not None and _region_bounds(plan, beat.region) is not None:
                if not _in_region(item, _region_bounds(plan, beat.region)):
                    findings.append(
                        _finding(
                            "BEAT_REGION_MISMATCH",
                            item,
                            beat_id=beat.id,
                            observed={"center_x": item.center_x, "center_y": item.center_y},
                            expected={"region": beat.region},
                            explanation="The beat's object is not observed in its planned region.",
                            suggestion=f"Move the beat object into the {beat.region!r} region.",
                        )
                    )
            if beat.text is not None and item.text != beat.text:
                findings.append(
                    _finding(
                        "BEAT_TEXT_MISMATCH",
                        item,
                        beat_id=beat.id,
                        observed={"text": item.text},
                        expected={"text": beat.text},
                        explanation="The observed text differs from the authored beat.",
                        suggestion="Use the exact text from the ScenePlan.",
                    )
                )
            if beat.formula is not None and item.formula != beat.formula:
                findings.append(
                    _finding(
                        "BEAT_FORMULA_MISMATCH",
                        item,
                        beat_id=beat.id,
                        observed={"formula": item.formula},
                        expected={"formula": beat.formula},
                        explanation="The observed formula differs from the authored beat.",
                        suggestion="Use the exact formula from the ScenePlan.",
                    )
                )

    if plan.expectations is not None and observed.frames:
        reasons = check_expectations(observed.frames, plan.expectations)
        for reason in reasons:
            findings.append(
                QualityFinding(
                    code="PLAN_EXPECTATION_MISMATCH",
                    severity="failure",
                    scene_id=plan.id,
                    observed={"reason": reason},
                    expected={"expectations": plan.expectations.model_dump(mode="json")},
                    explanation=(
                        "Pixel observations do not satisfy the semantic expectation contract."
                    ),
                    suggestion=(
                        "Correct the generated scene to satisfy the declared expectation beat."
                    ),
                )
            )


def _finding(
    code: str,
    item: ObservedObject,
    *,
    observed: dict[str, object],
    expected: dict[str, object],
    explanation: str,
    suggestion: str,
    severity: str = "failure",
    beat_id: str | None = None,
) -> QualityFinding:
    return QualityFinding(
        code=code,
        severity=severity,  # type: ignore[arg-type]
        scene_id="visual-scene",
        beat_id=beat_id,
        instant_seconds=item.logical_time,
        object_ids=[item.id],
        observed=observed,
        expected=expected,
        explanation=explanation,
        suggestion=suggestion,
    )


def _deduplicate(
    findings: list[QualityFinding], *, scene_id: str | None = None
) -> list[QualityFinding]:
    """Remove repeated state observations while preserving a scene identity."""
    normalized: list[QualityFinding] = []
    seen: set[tuple[str, str | None, tuple[str, ...], str]] = set()
    for finding in findings:
        if scene_id is not None:
            object.__setattr__(finding, "scene_id", scene_id)
        key = (finding.code, finding.beat_id, tuple(finding.object_ids), str(finding.observed))
        if key in seen:
            continue
        seen.add(key)
        normalized.append(finding)
    return normalized


def _unique_objects(observed: ObservedScene) -> list[ObservedObject]:
    result: list[ObservedObject] = []
    seen: set[tuple[str, float, str]] = set()
    for item in [
        *observed.initial_state,
        *observed.final_state,
        *(obj for state in observed.checkpoints for obj in state.objects),
    ]:
        key = (item.id, item.logical_time, item.bbox.model_dump_json())
        if key not in seen:
            seen.add(key)
            result.append(item)
    return result


def _states(observed: ObservedScene) -> list[list[ObservedObject]]:
    states = [checkpoint.objects for checkpoint in observed.checkpoints]
    if not states:
        states = [observed.initial_state, observed.final_state]
    return [state for state in states if state]


def _timeline_states(
    observed: ObservedScene,
) -> list[tuple[float, str | None, dict[str, ObservedObject]]]:
    """Return ordered, identity-indexed snapshots for temporal critics.

    Runtime checkpoints are the strongest evidence because they carry logical
    times and authored beat IDs.  A hand-built observation may only provide
    initial/final states, so those states remain a deterministic fallback for
    the unit-test and sensor seam.  Empty checkpoints are retained: they are
    useful evidence that a beat had not yet introduced its required objects.
    """

    if observed.checkpoints:
        timeline = [
            (
                checkpoint.instant_seconds,
                checkpoint.beat_id,
                {item.id: item for item in checkpoint.objects},
            )
            for checkpoint in sorted(
                observed.checkpoints,
                key=lambda checkpoint: checkpoint.instant_seconds,
            )
        ]
        final = {item.id: item for item in observed.final_state}
        observed_duration = _observed_duration(observed) or 0.0
        if final and (
            not timeline or timeline[-1][2] != final or timeline[-1][0] < observed_duration
        ):
            timeline.append((observed_duration, None, final))
        return timeline

    initial = {item.id: item for item in observed.initial_state}
    final = {item.id: item for item in observed.final_state}
    observed_duration = _observed_duration(observed) or 0.0
    if initial == final:
        if not initial:
            return []
        if observed_duration > 0:
            return [(0.0, None, initial), (observed_duration, None, final)]
        return [(0.0, None, initial)]
    timeline: list[tuple[float, str | None, dict[str, ObservedObject]]] = []
    if initial:
        timeline.append((0.0, None, initial))
    if final:
        timeline.append((observed_duration, None, final))
    return timeline


def _find_beat_state(
    timeline: list[tuple[float, str | None, dict[str, ObservedObject]]],
    beat: Beat,
    *,
    start: int,
    stop: int | None = None,
) -> int | None:
    """Find the first checkpoint satisfying one beat's IDs and authored order."""

    end = len(timeline) if stop is None else min(stop, len(timeline))
    candidates = range(max(start, 0), end)
    # A runtime beat ID is explicit evidence and wins over a coincidental
    # object set.  If no matching ID exists, object/order matching is the
    # compatibility path for observations produced by older fakes.
    all_candidates = list(range(max(start, 0), end))
    identified = [
        index for index in all_candidates if beat.id is not None and timeline[index][1] == beat.id
    ]
    candidates = identified + [index for index in all_candidates if index not in identified]
    expected = list(beat.objects)
    for index in candidates:
        _instant, _checkpoint_beat_id, objects = timeline[index]
        if not expected:
            if beat.id is None or _checkpoint_beat_id == beat.id:
                return index
            continue
        if not all(object_id in objects for object_id in expected):
            continue
        observed_order = [object_id for object_id in objects if object_id in expected]
        if observed_order == expected:
            return index
    return None


def _movement_matches(
    previous: ObservedObject,
    current: ObservedObject,
    movement: str,
) -> bool:
    """Check a declared movement against measured checkpoint geometry."""

    label = movement.lower()
    delta_x = current.center_x - previous.center_x
    delta_y = current.center_y - previous.center_y
    displacement = math.hypot(delta_x, delta_y)
    epsilon = 0.02
    if any(token in label for token in ("rotate", "rotation", "turn")):
        return abs(current.orientation - previous.orientation) > 0.05
    if any(token in label for token in ("scale", "grow", "shrink", "resize")):
        width_change = abs(current.width / max(previous.width, epsilon) - 1.0)
        height_change = abs(current.height / max(previous.height, epsilon) - 1.0)
        return max(width_change, height_change) > 0.05
    if any(token in label for token in ("right", "east", "x+", "positive x")):
        return delta_x > epsilon
    if any(token in label for token in ("left", "west", "x-", "negative x")):
        return delta_x < -epsilon
    # Frame coordinates grow downward, hence up/north means a negative y
    # delta and down/south means a positive y delta.
    if any(token in label for token in ("up", "north", "y-", "negative y")):
        return delta_y < -epsilon
    if any(token in label for token in ("down", "south", "y+", "positive y")):
        return delta_y > epsilon
    return displacement > epsilon


def _plan_object_textual(item: VisualObject | None) -> bool:
    if item is None:
        return False
    return (
        item.text is not None
        or item.formula is not None
        or item.kind.lower() in {"text", "tex", "mathtex", "formula"}
    )


def _is_textual(item: ObservedObject) -> bool:
    return bool(
        item.text
        or item.formula
        or item.kind.lower() in {"text", "tex", "mathtex", "formula", "label"}
    )


def _is_formula(item: ObservedObject) -> bool:
    return bool(item.formula) or "math" in item.kind.lower() or item.kind.lower() == "formula"


def _is_plan_textual(item: VisualObject | None) -> bool:
    return _plan_object_textual(item)


def _animation_duration(beat: Beat, theme: VideoTheme) -> float:
    action = beat.action.lower()
    if "transform" in action:
        return theme.animation_durations["transform"]
    if "emphas" in action:
        return theme.animation_durations["emphasis"]
    if "fade" in action:
        return theme.animation_durations["fade"]
    return theme.animation_durations["create"]


def _required_read_seconds(plan: ScenePlan, object_id: str, theme: VideoTheme) -> float:
    values = [
        beat.min_read_seconds
        for beat in plan.beats
        if object_id in beat.objects and beat.min_read_seconds is not None
    ]
    return max(values, default=theme.min_read_duration)


def _present_seconds(plan: ScenePlan, object_id: str) -> float | None:
    values = [beat.effective_duration for beat in plan.beats if object_id in beat.objects]
    if not values:
        return None
    return sum(values)


def _observed_duration(observed: ObservedScene) -> float | None:
    values = [checkpoint.instant_seconds for checkpoint in observed.checkpoints]
    values.extend(animation.end_seconds for animation in observed.animations)
    return max(values) if values else None


def _observed_present_seconds(observed: ObservedScene, object_id: str) -> float | None:
    """Measure how long an object remains present in ordered snapshots."""

    timeline = _timeline_states(observed)
    if len(timeline) < 2:
        return None
    duration = 0.0
    for previous, current in zip(timeline, timeline[1:], strict=False):
        instant, _beat_id, objects = previous
        next_instant = current[0]
        item = objects.get(object_id)
        if item is not None and item.visible and item.width > 0 and item.height > 0:
            duration += max(0.0, next_instant - instant)
    return duration


def _overlap_allowed(
    left: VisualObject | None,
    right: VisualObject | None,
    observed: ObservedScene,
    *,
    object_ids: set[str],
    instant: float,
    plan: ScenePlan,
) -> bool:
    if left is None or right is None:
        return False
    if left.group_id is not None and left.group_id == right.group_id:
        return True
    if left.overlap_policy in {"intentional", "group", "background"}:
        return True
    if right.overlap_policy in {"intentional", "group", "background"}:
        return True
    if left.overlap_policy == "transient" or right.overlap_policy == "transient":
        return any(
            object_ids.issubset(animation.object_ids)
            and animation.start_seconds - 1e-6 <= instant <= animation.end_seconds + 1e-6
            for animation in observed.animations
        )
    if plan.layout.allow_background_overlap and (
        left.kind.lower() == "background" or right.kind.lower() == "background"
    ):
        return True
    return False


def _intersection(left: BoundingBox, right: BoundingBox) -> float:
    width = max(0.0, min(left.right, right.right) - max(left.left, right.left))
    height = max(0.0, min(left.bottom, right.bottom) - max(left.top, right.top))
    return width * height


def _edge_gap(left: BoundingBox, right: BoundingBox) -> float:
    """Return Euclidean edge-to-edge distance for two non-overlapping boxes."""

    horizontal = max(left.left - right.right, right.left - left.right, 0.0)
    vertical = max(left.top - right.bottom, right.top - left.bottom, 0.0)
    return math.hypot(horizontal, vertical)


def _iou(left: BoundingBox, right: BoundingBox) -> float:
    intersection = _intersection(left, right)
    union = left.width * left.height + right.width * right.height - intersection
    return intersection / union if union else 0.0


def _box_document(box: BoundingBox) -> dict[str, float]:
    return {
        "left_edge": box.left,
        "top_edge": box.top,
        "right_edge": box.right,
        "bottom_edge": box.bottom,
    }


def _region_bounds(plan: ScenePlan, name: str) -> SemanticRegion | None:
    return plan.theme.regions.get(name)


def _in_region(item: ObservedObject, region: SemanticRegion | None) -> bool:
    if region is None:
        return True
    return (
        region.left <= item.center_x <= region.right
        and region.top <= item.center_y <= region.bottom
    )


def _rgb(value: str) -> tuple[int, int, int] | None:
    color = _normalise_color(value)
    if color is None:
        return None
    return tuple(int(color[offset : offset + 2], 16) for offset in (1, 3, 5))


def _normalise_color(value: str) -> str | None:
    candidate = value.strip()
    if candidate.lower() in _NAMED_COLORS:
        return _NAMED_COLORS[candidate.lower()]
    if _HEX_COLOR.fullmatch(candidate):
        return candidate.upper()
    return None


def _pixel_color(
    item: ObservedObject,
    observed: ObservedScene,
) -> tuple[str | None, bool, list[dict[str, object]]]:
    """Resolve colour from HSV/frame evidence, with a bounded ambiguity record."""

    if not observed.frames:
        return (
            _normalise_color(item.observed_color) if item.observed_color is not None else None,
            False,
            [],
        )
    candidates: list[tuple[float, int, ObservedShape]] = []
    for frame in observed.frames:
        for shape in frame.shapes:
            if not _pixel_shape_matches(item, shape):
                continue
            distance = math.hypot(
                shape.center_x - item.center_x,
                shape.center_y - item.center_y,
            )
            tolerance = max(0.08, math.hypot(item.width, item.height) / 2 + 0.08)
            if distance <= tolerance:
                candidates.append((distance, frame.index, shape))
    if not candidates:
        return None, False, []
    candidates.sort(key=lambda value: (value[0], value[1], value[2].color))
    colour_counts: dict[str, int] = {}
    for _distance, _frame_index, shape in candidates:
        colour = shape.color.lower()
        colour_counts[colour] = colour_counts.get(colour, 0) + 1
    dominant_colour = max(
        colour_counts,
        key=lambda colour: (colour_counts[colour], colour),
    )
    dominant = [
        candidate for candidate in candidates if candidate[2].color.lower() == dominant_colour
    ]
    dominant.sort(key=lambda value: (value[0], value[1], -value[2].area_fraction))
    dominant_count = colour_counts[dominant_colour]
    ambiguous = sum(count == dominant_count for count in colour_counts.values()) > 1
    evidence = [
        {
            "frame": frame_index,
            "distance": round(distance, 4),
            "color": shape.color,
            "rgb": list(shape.observed_rgb) if shape.observed_rgb is not None else None,
        }
        for distance, frame_index, shape in candidates[:8]
    ]
    return _shape_pixel_hex(dominant[0][2]), ambiguous, evidence


def _pixel_shape_matches(item: ObservedObject, shape: ObservedShape) -> bool:
    item_kind = item.kind.lower()
    shape_kind = shape.kind.lower()
    if item_kind == shape_kind:
        return True
    if _is_textual(item) and shape_kind in {
        "text",
        "tex",
        "mathtex",
        "formula",
        "label",
        "circle",
        "square",
        "polygon",
    }:
        return True
    return item_kind in {"arrow", "vector", "line"} and shape_kind in {
        "arrow",
        "line",
        "polygon",
    }


def _shape_pixel_hex(shape: ObservedShape) -> str | None:
    if shape.observed_rgb is not None:
        return "#" + "".join(f"{channel:02X}" for channel in shape.observed_rgb)
    return _normalise_color(shape.color)


def _colour_distance(first: str, second: str) -> float:
    left = _rgb(first)
    right = _rgb(second)
    if left is None or right is None:
        return 1.0
    return math.sqrt(sum((a - b) ** 2 for a, b in zip(left, right, strict=True))) / (
        255 * math.sqrt(3)
    )


__all__ = [
    "check_contrast",
    "check_legibility",
    "check_overlaps",
    "check_plan_coherence",
    "check_rhythm",
    "check_safe_area",
    "contrast_ratio",
    "evaluate_visual_quality",
    "relative_luminance",
]
