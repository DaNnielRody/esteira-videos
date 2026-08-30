"""Observable Manim runtime and the versioned facts it emits.

Generated scenes use :class:`VisualScene` and call ``register_visual`` for
objects that matter to the plan.  The class records logical checkpoints around
``play``/``wait`` and writes one JSON document at render teardown.  The
contracts are independent of Manim, so tests can construct the same facts with
plain Python values.
"""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import numpy as np
from pydantic import BaseModel, ConfigDict, Field, model_validator

from video_pipeline.observation import FrameObservation, ObservedShape
from video_pipeline.scene_plan import ScenePlan

try:  # pragma: no cover - exercised by the real-Manim integration suite
    from manim import Scene as _ManimScene
except ImportError:  # pragma: no cover - local unit tests may omit Manim

    class _ManimScene:
        """Small fallback that keeps the instrumentation importable."""

        def __init__(self, *_args: object, **_kwargs: object) -> None:
            self.mobjects: list[object] = []

        def add(self, *mobjects: object) -> _ManimScene:
            self.mobjects.extend(mobjects)
            return self

        def remove(self, *mobjects: object) -> _ManimScene:
            self.mobjects = [item for item in self.mobjects if item not in mobjects]
            return self

        def play(self, *_animations: object, **_kwargs: object) -> None:
            return None

        def wait(self, *_args: object, **_kwargs: object) -> None:
            return None

        def render(self, *_args: object, **_kwargs: object) -> None:
            return None


class BoundingBox(BaseModel):
    """Normalised left/top/right/bottom bounds of one visual object."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    left: float = Field(ge=-10.0, le=10.0)
    top: float = Field(ge=-10.0, le=10.0)
    right: float = Field(ge=-10.0, le=10.0)
    bottom: float = Field(ge=-10.0, le=10.0)

    @model_validator(mode="after")
    def _bounds_are_ordered(self) -> BoundingBox:
        if self.left > self.right or self.top > self.bottom:
            raise ValueError("bounding box bounds must be ordered")
        return self

    @property
    def width(self) -> float:
        return self.right - self.left

    @property
    def height(self) -> float:
        return self.bottom - self.top


class CameraState(BaseModel):
    """Camera pose and frame dimensions at one logical checkpoint."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    center_x: float = 0.0
    center_y: float = 0.0
    frame_width: float = Field(default=14.0, gt=0.0)
    frame_height: float = Field(default=8.0, gt=0.0)
    orientation: tuple[float, float, float] = (0.0, 0.0, 0.0)

    @model_validator(mode="before")
    @classmethod
    def _normalise_json_orientation(cls, value: object) -> object:
        if isinstance(value, dict) and isinstance(value.get("orientation"), list):
            data = dict(value)
            data["orientation"] = tuple(data["orientation"])
            return data
        return value


class ObservedObject(BaseModel):
    """One logical object measured by the runtime at a checkpoint."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    id: str
    kind: str
    bbox: BoundingBox
    center_x: float = Field(ge=-10.0, le=10.0)
    center_y: float = Field(ge=-10.0, le=10.0)
    width: float = Field(ge=0.0, le=20.0)
    height: float = Field(ge=0.0, le=20.0)
    color_role: str | None = None
    observed_color: str | None = None
    text: str | None = None
    formula: str | None = None
    visible: bool = True
    z_index: int = 0
    logical_time: float = Field(default=0.0, ge=0.0)
    orientation: float = 0.0


class AnimationFact(BaseModel):
    """One recorded animation interval and its participating object IDs."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    name: str
    object_ids: list[str] = Field(default_factory=list)
    start_seconds: float = Field(ge=0.0)
    end_seconds: float = Field(ge=0.0)
    run_time: float = Field(gt=0.0)
    initial_objects: list[ObservedObject] = Field(default_factory=list)
    final_objects: list[ObservedObject] = Field(default_factory=list)

    @model_validator(mode="after")
    def _interval_is_valid(self) -> AnimationFact:
        if self.end_seconds < self.start_seconds:
            raise ValueError("animation end must be after start")
        if abs((self.end_seconds - self.start_seconds) - self.run_time) > 1e-5:
            raise ValueError("animation interval must match run_time")
        return self


class SceneCheckpoint(BaseModel):
    """A named logical snapshot used by plan and rhythm critics."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    id: str
    beat_id: str | None = None
    instant_seconds: float = Field(ge=0.0)
    objects: list[ObservedObject] = Field(default_factory=list)
    camera: CameraState = Field(default_factory=CameraState)
    visual_change: bool = True


class ObservedScene(BaseModel):
    """Versioned logical and pixel evidence for one rendered scene."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_version: str = "visual.observed-scene/1"
    scene_id: str
    scene_name: str
    theme_id: str | None = None
    initial_state: list[ObservedObject] = Field(default_factory=list)
    final_state: list[ObservedObject] = Field(default_factory=list)
    checkpoints: list[SceneCheckpoint] = Field(default_factory=list)
    animations: list[AnimationFact] = Field(default_factory=list)
    camera_initial: CameraState = Field(default_factory=CameraState)
    camera_final: CameraState = Field(default_factory=CameraState)
    frames: list[FrameObservation] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _normalise_frame_documents(cls, value: object) -> object:
        if not isinstance(value, Mapping):
            return value
        data = dict(value)
        raw_frames = data.get("frames")
        if isinstance(raw_frames, list):
            data["frames"] = [_frame_from_document(raw_frame) for raw_frame in raw_frames]
        return data

    @model_validator(mode="after")
    def _schema_is_known(self) -> ObservedScene:
        if self.schema_version != "visual.observed-scene/1":
            raise ValueError("unsupported observed-scene schema version")
        return self

    def to_document(self) -> dict[str, object]:
        """Return JSON data preserving logical and pixel facts together."""

        return self.model_dump(mode="json")

    @classmethod
    def from_document(cls, document: object) -> ObservedScene:
        """Validate one persisted facts document."""

        return cls.model_validate(document)


def _frame_from_document(value: object) -> object:
    """Convert JSON frame dictionaries to the dataclass sensor contract."""

    if isinstance(value, FrameObservation):
        return value
    if not isinstance(value, Mapping):
        return value
    data = dict(value)
    raw_shapes = data.get("shapes")
    if not isinstance(raw_shapes, list):
        return value
    shapes: list[ObservedShape] = []
    for raw_shape in raw_shapes:
        if isinstance(raw_shape, ObservedShape):
            shapes.append(raw_shape)
            continue
        if not isinstance(raw_shape, Mapping):
            return value
        shape_data = dict(raw_shape)
        rgb = shape_data.get("observed_rgb")
        if isinstance(rgb, list) and len(rgb) == 3:
            shape_data["observed_rgb"] = tuple(rgb)
        shapes.append(ObservedShape(**shape_data))
    data["shapes"] = shapes
    return FrameObservation(**data)


class _MobjectLike(Protocol):
    """Minimum shape of a Manim mobject used by the recorder."""

    def get_center(self) -> object: ...

    def get_bounding_box(self) -> object: ...


@dataclass(slots=True)
class _Registration:
    mobject: object
    object_id: str
    kind: str
    color_role: str | None
    text: str | None
    formula: str | None
    z_index: int


class VisualScene(_ManimScene):
    """Manim ``Scene`` with semantic object and animation instrumentation."""

    def __init__(
        self, *args: object, scene_plan: ScenePlan | None = None, **kwargs: object
    ) -> None:
        super().__init__(*args, **kwargs)
        configured_plan = os.environ.get("VIDEO_PIPELINE_SCENE_PLAN")
        if scene_plan is None and configured_plan:
            try:
                scene_plan = ScenePlan.model_validate_json(configured_plan)
            except ValueError:
                # The renderer still emits facts when a malformed optional plan
                # is supplied; the pipeline records the plan validation error.
                scene_plan = None
        self.scene_plan = scene_plan
        self._registrations: dict[int, _Registration] = {}
        self._checkpoints: list[SceneCheckpoint] = []
        self._animations: list[AnimationFact] = []
        self._logical_time = 0.0
        self._initial_state: list[ObservedObject] = []
        self._initial_captured = False
        self._initial_camera = self._camera_state()
        # Manim constructs the scene after ``__init__`` returns.  Capture the
        # empty/pre-construct state here so a first ``add`` or ``play`` cannot
        # accidentally become the logical initial state.
        self._capture_initial_state()

    def register_visual(
        self,
        mobject: object,
        object_id: str,
        *,
        kind: str | None = None,
        color_role: str | None = None,
        text: str | None = None,
        formula: str | None = None,
        z_index: int = 0,
    ) -> object:
        """Attach a semantic identity to one mobject before adding it."""

        if not object_id.strip():
            raise ValueError("visual object ID must not be blank")
        if text is not None and formula is not None:
            raise ValueError("visual registration cannot contain text and formula")
        registration = _Registration(
            mobject=mobject,
            object_id=object_id,
            kind=kind or type(mobject).__name__.lower(),
            color_role=color_role,
            text=text,
            formula=formula,
            z_index=z_index,
        )
        self._registrations[id(mobject)] = registration
        return mobject

    def checkpoint(
        self,
        checkpoint_id: str,
        *,
        beat_id: str | None = None,
        visual_change: bool = True,
    ) -> SceneCheckpoint:
        """Persist the current logical state at a named plan checkpoint."""

        checkpoint = SceneCheckpoint(
            id=checkpoint_id,
            beat_id=beat_id,
            instant_seconds=self._logical_time,
            objects=self._snapshot(),
            camera=self._camera_state(),
            visual_change=visual_change,
        )
        self._checkpoints.append(checkpoint)
        if not self._initial_captured:
            self._initial_state = list(checkpoint.objects)
            self._initial_captured = True
        return checkpoint

    def add(self, *mobjects: object) -> VisualScene:
        """Add mobjects while preserving their semantic registrations."""

        result = super().add(*mobjects)
        return result if isinstance(result, VisualScene) else self

    def remove(self, *mobjects: object) -> VisualScene:
        """Remove mobjects without deleting their identity history."""

        result = super().remove(*mobjects)
        return result if isinstance(result, VisualScene) else self

    def play(self, *animations: object, **kwargs: object) -> object:
        """Record animation bounds around the real Manim ``play`` call."""

        start = self._logical_time
        initial = self._snapshot()
        result = super().play(*animations, **kwargs)
        run_time_value = kwargs.get("run_time")
        if run_time_value is None:
            run_time_value = max(
                (
                    float(getattr(animation, "run_time", 1.0))
                    for animation in animations
                    if isinstance(getattr(animation, "run_time", 1.0), (int, float))
                ),
                default=1.0,
            )
        run_time = float(run_time_value) if isinstance(run_time_value, (int, float)) else 1.0
        self._logical_time += max(run_time, 1e-6)
        final = self._snapshot()
        object_ids = self._animation_object_ids(animations)
        names = [type(animation).__name__ for animation in animations]
        self._animations.append(
            AnimationFact(
                name="+".join(names) or "play",
                object_ids=object_ids,
                start_seconds=start,
                end_seconds=self._logical_time,
                run_time=max(run_time, 1e-6),
                initial_objects=initial,
                final_objects=final,
            )
        )
        return result

    def wait(self, duration: float = 0.0, *args: object, **kwargs: object) -> object:
        """Advance logical time for a hold without inventing a visual change."""

        result = super().wait(duration, *args, **kwargs)
        if duration > 0:
            self._logical_time += duration
        return result

    def render(self, *args: object, **kwargs: object) -> object:
        """Run Manim and persist facts even when rendering raises."""

        self._capture_initial_state()
        try:
            return super().render(*args, **kwargs)
        finally:
            self.write_observation(self._observation_path())

    def write_observation(self, path: str | Path) -> ObservedScene:
        """Write the current facts atomically and return the same contract."""

        observed = self._observed_scene()
        destination = Path(path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=destination.parent, delete=False
        ) as handle:
            json.dump(observed.to_document(), handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            temporary = Path(handle.name)
        temporary.replace(destination)
        return observed

    def _observation_path(self) -> Path:
        configured = os.environ.get("VIDEO_PIPELINE_OBSERVATION_PATH")
        if configured:
            return Path(configured)
        media = os.environ.get("VIDEO_PIPELINE_MEDIA_DIR")
        return Path(media or ".") / "visual-facts.json"

    def _capture_initial_state(self) -> None:
        """Capture the pre-construct state exactly once."""

        if self._initial_captured:
            return
        self._initial_state = self._snapshot()
        self._checkpoints.append(
            SceneCheckpoint(
                id="initial",
                instant_seconds=self._logical_time,
                objects=list(self._initial_state),
                camera=self._camera_state(),
                visual_change=False,
            )
        )
        self._initial_captured = True

    def _observed_scene(self) -> ObservedScene:
        final = self._snapshot()
        if not self._checkpoints:
            self._capture_initial_state()
            self.checkpoint("final", visual_change=True)
        elif self._checkpoints[-1].instant_seconds != self._logical_time:
            self.checkpoint("final", visual_change=True)
        return ObservedScene(
            scene_id=self.scene_plan.id
            if self.scene_plan is not None
            else type(self).__name__.lower(),
            scene_name=type(self).__name__,
            theme_id=self.scene_plan.theme.id if self.scene_plan is not None else None,
            initial_state=self._initial_state,
            final_state=final,
            checkpoints=list(self._checkpoints),
            animations=list(self._animations),
            camera_initial=self._initial_camera,
            camera_final=self._camera_state(),
        )

    def _snapshot(self) -> list[ObservedObject]:
        visible = {id(item) for item in getattr(self, "mobjects", [])}
        facts: list[ObservedObject] = []
        for registration in self._registrations.values():
            if id(registration.mobject) not in visible:
                continue
            facts.append(self._describe_registration(registration))
        return facts

    def _describe_registration(self, registration: _Registration) -> ObservedObject:
        mobject = registration.mobject
        camera = self._camera_state()
        try:
            bounding = np.asarray(mobject.get_bounding_box(), dtype=float)  # type: ignore[attr-defined]
            minimum = bounding.min(axis=0)
            maximum = bounding.max(axis=0)
            center = (minimum + maximum) / 2.0
        except (AttributeError, TypeError, ValueError):
            try:
                center = np.asarray(mobject.get_center(), dtype=float)  # type: ignore[attr-defined]
            except (AttributeError, TypeError, ValueError):
                center = np.zeros(3, dtype=float)
            minimum = center - np.array([0.0, 0.0, 0.0])
            maximum = center + np.array([0.0, 0.0, 0.0])
        left = (float(minimum[0]) - camera.center_x) / camera.frame_width + 0.5
        right = (float(maximum[0]) - camera.center_x) / camera.frame_width + 0.5
        top = 0.5 - (float(maximum[1]) - camera.center_y) / camera.frame_height
        bottom = 0.5 - (float(minimum[1]) - camera.center_y) / camera.frame_height
        observed_color = None
        try:
            color = mobject.get_color()  # type: ignore[attr-defined]
            observed_color = color.to_hex() if hasattr(color, "to_hex") else str(color)
        except AttributeError:
            pass
        orientation = 0.0
        try:
            angle = mobject.get_angle()  # type: ignore[attr-defined]
            orientation = float(angle)
        except (AttributeError, TypeError, ValueError):
            pass
        return ObservedObject(
            id=registration.object_id,
            kind=registration.kind,
            bbox=BoundingBox(left=left, top=top, right=right, bottom=bottom),
            center_x=(left + right) / 2.0,
            center_y=(top + bottom) / 2.0,
            width=max(0.0, right - left),
            height=max(0.0, bottom - top),
            color_role=registration.color_role,
            observed_color=observed_color,
            text=registration.text,
            formula=registration.formula,
            visible=True,
            z_index=registration.z_index,
            logical_time=self._logical_time,
            orientation=orientation,
        )

    def _camera_state(self) -> CameraState:
        camera = getattr(self, "camera", None)
        frame_width = getattr(camera, "frame_width", 14.0)
        frame_height = getattr(camera, "frame_height", 8.0)
        frame_center = getattr(camera, "frame_center", (0.0, 0.0, 0.0))
        try:
            center_x = float(frame_center[0])
            center_y = float(frame_center[1])
        except (IndexError, TypeError):
            center_x, center_y = 0.0, 0.0
        return CameraState(
            center_x=center_x,
            center_y=center_y,
            frame_width=float(frame_width),
            frame_height=float(frame_height),
        )

    def _animation_object_ids(self, animations: tuple[object, ...]) -> list[str]:
        found: list[str] = []
        for animation in animations:
            mobject = getattr(animation, "mobject", None)
            registration = self._registrations.get(id(mobject))
            if registration is not None and registration.object_id not in found:
                found.append(registration.object_id)
        return found


__all__ = [
    "AnimationFact",
    "BoundingBox",
    "CameraState",
    "ObservedObject",
    "ObservedScene",
    "SceneCheckpoint",
    "VisualScene",
]
