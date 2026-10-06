"""Deterministic continuity state and scene-boundary comparison."""

from __future__ import annotations

import math
from collections.abc import Iterable
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from video_pipeline.quality import QualityFinding, QualityReport
from video_pipeline.runtime import CameraState, ObservedScene
from video_pipeline.scene_plan import ScenePlan
from video_pipeline.theme import VideoTheme

ContinuityChange = Literal[
    "theme",
    "background",
    "camera",
    "scale",
    "objects",
    "colors",
    "typography",
    "persistent_elements",
    "transition",
]


class ContinuityObject(BaseModel):
    """Identity and presentation facts for an object recurring across scenes."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    id: str
    identity: str
    kind: str
    color_role: str | None = None
    scale: float = Field(default=1.0, gt=0.0)

    @field_validator("id", "identity", "kind")
    @classmethod
    def _labels_are_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("continuity object labels must not be blank")
        return value


class ContinuityState(BaseModel):
    """State handed from one accepted scene to the next scene's plan."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_version: Literal["visual.continuity/1"] = "visual.continuity/1"
    scene_id: str
    theme_id: str
    theme: VideoTheme | None = None
    background: str
    camera: CameraState = Field(default_factory=CameraState)
    visual_scale: float = Field(default=1.0, gt=0.0)
    recurring_objects: list[ContinuityObject] = Field(default_factory=list)
    color_roles: dict[str, str] = Field(default_factory=dict)
    typography: dict[str, str] = Field(default_factory=dict)
    persistent_elements: list[str] = Field(default_factory=list)
    transition: str = "default"
    transition_required: bool = False
    observed_transition: str | None = None

    @field_validator("scene_id", "theme_id", "background", "transition")
    @classmethod
    def _labels_are_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("continuity state labels must not be blank")
        return value

    @field_validator("persistent_elements")
    @classmethod
    def _persistent_ids_are_unique(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("persistent element IDs must be unique")
        return value

    def to_document(self) -> dict[str, object]:
        """Return JSON facts for the scene boundary artifact."""

        return self.model_dump(mode="json")

    @classmethod
    def from_scene(
        cls,
        plan: ScenePlan,
        observed: ObservedScene,
        *,
        state: Literal["initial", "final"] = "final",
    ) -> ContinuityState:
        """Build continuity facts directly from one plan and observed scene."""

        source = observed.initial_state if state == "initial" else observed.final_state
        if state == "initial" and not source:
            # ``VisualScene`` records the true pre-construct state (usually
            # empty), then its first meaningful checkpoint.  Continuity needs
            # the latter so a recurring object can be compared without
            # corrupting the initial-state evidence.
            source = next(
                (checkpoint.objects for checkpoint in observed.checkpoints if checkpoint.objects),
                [],
            )
        boundary_plan = plan.continuity_in if state == "initial" else plan.continuity_out
        recurring_ids = (
            set(boundary_plan.recurring_object_ids) if boundary_plan is not None else set()
        )
        source = [item for item in source if item.id in recurring_ids]
        recurring = [
            ContinuityObject(
                id=item.id,
                identity=f"{item.kind}:{item.id}",
                kind=item.kind,
                color_role=item.color_role,
                scale=max(item.width, item.height, 0.001),
            )
            for item in source
        ]
        scales = [item.scale for item in recurring]
        return cls(
            scene_id=plan.id,
            theme_id=plan.theme.id,
            theme=plan.theme,
            background=plan.theme.background_color,
            visual_scale=sum(scales) / len(scales) if scales else 1.0,
            recurring_objects=recurring,
            color_roles=dict(plan.theme.palette),
            typography=dict(plan.theme.font_families),
            persistent_elements=[item.id for item in plan.objects if item.group_id == "persistent"],
            transition=(
                boundary_plan.expected_transition if boundary_plan is not None else "default"
            ),
            transition_required=boundary_plan.required if boundary_plan is not None else False,
            observed_transition=_observed_transition(observed, state),
        )


def compare_continuity(
    previous: ContinuityState,
    current: ContinuityState,
    *,
    declared_changes: Iterable[str] = (),
    attempt: int = 1,
) -> QualityReport:
    """Compare accepted previous state with the next scene's initial state."""

    allowed = set(declared_changes)
    findings: list[QualityFinding] = []
    if previous.theme_id != current.theme_id and not _allowed(allowed, "theme"):
        findings.append(
            _state_finding(
                "CONTINUITY_THEME_CHANGED",
                current,
                observed={"previous": previous.theme_id, "current": current.theme_id},
                expected={"theme_id": previous.theme_id},
                explanation="The visual theme changed at a scene boundary without declaration.",
                suggestion=(
                    "Keep one theme ID or declare the intentional theme transition in the plan."
                ),
            )
        )
    if previous.background != current.background and not _allowed(allowed, "background"):
        findings.append(
            _state_finding(
                "CONTINUITY_BACKGROUND_CHANGED",
                current,
                observed={"previous": previous.background, "current": current.background},
                expected={"background": previous.background},
                explanation=(
                    "The scene background changed without an explicit boundary declaration."
                ),
                suggestion="Use the prior theme background or declare the background change.",
            )
        )
    if _camera_jump(previous.camera, current.camera) and not _allowed(allowed, "camera"):
        findings.append(
            _state_finding(
                "CONTINUITY_CAMERA_JUMP",
                current,
                observed={
                    "previous": previous.camera.model_dump(),
                    "current": current.camera.model_dump(),
                },
                expected={"max_center_delta": 0.75, "max_scale_delta": 0.2},
                explanation="The camera pose or frame scale jumps at the scene boundary.",
                suggestion="Keep the camera pose or declare a camera transition in the plan.",
            )
        )
    if abs(current.visual_scale / previous.visual_scale - 1.0) > 0.25 and not _allowed(
        allowed, "scale"
    ):
        findings.append(
            _state_finding(
                "CONTINUITY_SCALE_JUMP",
                current,
                observed={"previous": previous.visual_scale, "current": current.visual_scale},
                expected={"relative_delta": 0.25},
                explanation="Recurring visual content changes scale abruptly between scenes.",
                suggestion="Preserve the visual scale or declare an intentional scale change.",
            )
        )

    previous_objects = {item.id: item for item in previous.recurring_objects}
    current_objects = {item.id: item for item in current.recurring_objects}
    for object_id in sorted(previous_objects.keys() - current_objects.keys()):
        if not _allowed(allowed, "objects"):
            findings.append(
                QualityFinding(
                    code="CONTINUITY_OBJECT_MISSING",
                    severity="failure",
                    scene_id=current.scene_id,
                    object_ids=[object_id],
                    observed={"current_object_ids": sorted(current_objects)},
                    expected={"object_id": object_id},
                    explanation="A recurring object from the previous scene is absent.",
                    suggestion=(
                        "Carry the recurring object into the next scene or declare its removal."
                    ),
                )
            )
    for object_id in sorted(previous_objects.keys() & current_objects.keys()):
        old, new = previous_objects[object_id], current_objects[object_id]
        if old.identity != new.identity or old.kind != new.kind:
            if not _allowed(allowed, "objects"):
                findings.append(
                    QualityFinding(
                        code="CONTINUITY_OBJECT_IDENTITY_CHANGED",
                        severity="failure",
                        scene_id=current.scene_id,
                        object_ids=[object_id],
                        observed={"previous": old.model_dump(), "current": new.model_dump()},
                        expected={"identity": old.identity, "kind": old.kind},
                        explanation="A recurring object has a different identity or kind.",
                        suggestion=(
                            "Reuse the same semantic object ID and visual kind across scenes."
                        ),
                    )
                )
        if old.color_role != new.color_role and not _allowed(allowed, "colors"):
            findings.append(
                QualityFinding(
                    code="CONTINUITY_COLOR_ROLE_CHANGED",
                    severity="failure",
                    scene_id=current.scene_id,
                    object_ids=[object_id],
                    observed={"previous": old.color_role, "current": new.color_role},
                    expected={"color_role": old.color_role},
                    explanation="A recurring object changed semantic colour role.",
                    suggestion="Keep the recurring object's colour role or declare the change.",
                )
            )
        relative_scale_change = abs(new.scale / old.scale - 1.0)
        if relative_scale_change > 0.25 and not _allowed(allowed, "scale"):
            findings.append(
                QualityFinding(
                    code="CONTINUITY_OBJECT_SCALE_CHANGED",
                    severity="failure",
                    scene_id=current.scene_id,
                    object_ids=[object_id],
                    observed={"previous_scale": old.scale, "current_scale": new.scale},
                    expected={"relative_delta": 0.25},
                    explanation="A recurring object's measured scale changes abruptly.",
                    suggestion=(
                        "Preserve the recurring object's scale or declare an intentional "
                        "scale change."
                    ),
                )
            )

    if previous.color_roles != current.color_roles and not _allowed(allowed, "colors"):
        findings.append(
            _state_finding(
                "CONTINUITY_COLOR_PALETTE_CHANGED",
                current,
                observed={"previous": previous.color_roles, "current": current.color_roles},
                expected={"color_roles": previous.color_roles},
                explanation="The semantic palette changed between adjacent scenes.",
                suggestion="Reuse the prior theme palette or declare a palette change.",
            )
        )
    if previous.typography != current.typography and not _allowed(allowed, "typography"):
        findings.append(
            _state_finding(
                "CONTINUITY_TYPOGRAPHY_CHANGED",
                current,
                observed={"previous": previous.typography, "current": current.typography},
                expected={"typography": previous.typography},
                explanation="The font family contract changed between adjacent scenes.",
                suggestion="Reuse the previous typography or declare a typographic transition.",
            )
        )
    if previous.persistent_elements != current.persistent_elements and not _allowed(
        allowed, "persistent_elements"
    ):
        findings.append(
            _state_finding(
                "CONTINUITY_PERSISTENT_ELEMENT_CHANGED",
                current,
                observed={
                    "previous": previous.persistent_elements,
                    "current": current.persistent_elements,
                },
                expected={"persistent_elements": previous.persistent_elements},
                explanation="A persistent element was removed or changed at the boundary.",
                suggestion="Carry the persistent element forward or declare its removal.",
            )
        )
    if previous.transition != current.transition and not _allowed(allowed, "transition"):
        findings.append(
            _state_finding(
                "CONTINUITY_TRANSITION_CHANGED",
                current,
                observed={"previous": previous.transition, "current": current.transition},
                expected={"transition": previous.transition},
                explanation="The boundary transition differs from the declared transition.",
                suggestion="Use the expected transition preset or declare the intentional change.",
            )
        )
    expected_transition = current.transition if current.transition_required else previous.transition
    transition_required = previous.transition_required or current.transition_required
    observed_transitions = {
        state.observed_transition
        for state in (previous, current)
        if state.observed_transition is not None
    }
    if (
        transition_required
        and observed_transitions
        and any(
            not _transition_matches(observed, expected_transition)
            for observed in observed_transitions
        )
        and not _allowed(allowed, "transition")
    ):
        findings.append(
            _state_finding(
                "CONTINUITY_TRANSITION_MISSING",
                current,
                observed={
                    "previous": previous.observed_transition,
                    "current": current.observed_transition,
                },
                expected={"transition": expected_transition},
                explanation=(
                    "The required scene-boundary transition was not observed in runtime animations."
                ),
                suggestion=(
                    "Implement the declared transition or mark the boundary as an intentional "
                    "change."
                ),
            )
        )
    return QualityReport(scene_id=current.scene_id, attempt=attempt, findings=findings)


def _state_finding(
    code: str,
    state: ContinuityState,
    *,
    observed: dict[str, object],
    expected: dict[str, object],
    explanation: str,
    suggestion: str,
) -> QualityFinding:
    return QualityFinding(
        code=code,
        severity="failure",
        scene_id=state.scene_id,
        observed=observed,
        expected=expected,
        explanation=explanation,
        suggestion=suggestion,
    )


def _allowed(allowed: set[str], change: ContinuityChange) -> bool:
    return change in allowed


def _camera_jump(previous: CameraState, current: CameraState) -> bool:
    center_delta = math.hypot(
        current.center_x - previous.center_x,
        current.center_y - previous.center_y,
    )
    width_delta = abs(current.frame_width / previous.frame_width - 1.0)
    height_delta = abs(current.frame_height / previous.frame_height - 1.0)
    return center_delta > 0.75 or max(width_delta, height_delta) > 0.2


def _observed_transition(
    observed: ObservedScene,
    state: Literal["initial", "final"],
) -> str:
    """Infer only coarse transitions that are explicit in runtime animation facts."""

    animations = observed.animations
    if not animations:
        return "none"
    candidate = animations[0] if state == "initial" else animations[-1]
    name = candidate.name.lower()
    if "fade" in name:
        return "fade"
    if "slide" in name:
        return "slide"
    return "none"


def _transition_matches(observed: str, expected: str) -> bool:
    if expected == "default":
        return observed == "fade"
    return observed == expected


__all__ = [
    "ContinuityChange",
    "ContinuityObject",
    "ContinuityState",
    "compare_continuity",
]
