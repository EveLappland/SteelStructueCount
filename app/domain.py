"""Version 2 contracts. Coordinates describe evidenced reference lines, not pixels."""
from __future__ import annotations

from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator


class Contract(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False, str_strip_whitespace=True)


class Evidence(Contract):
    id: str
    page_id: str
    kind: Literal['TEXT', 'VECTOR', 'MANUAL']
    bbox: list[float] = Field(min_length=4, max_length=4)
    text: str = ''
    points: list[list[float]] = Field(default_factory=list)
    style: dict = Field(default_factory=dict)


class Axis(Contract):
    label: str
    direction: Literal['X', 'Y']
    value_mm: float | None = None
    evidence_ids: list[str] = Field(default_factory=list)
    aliases: list[str] = Field(default_factory=list)


class Dimension(Contract):
    direction: Literal['X', 'Y']
    from_label: str
    to_label: str
    distance_mm: float
    evidence_ids: list[str] = Field(default_factory=list)


class CoordinateFrame(Contract):
    id: str = 'project'
    axes: list[Axis] = Field(default_factory=list)
    dimensions: list[Dimension] = Field(default_factory=list)
    z_origin_m: float = 0
    origin_evidence_ids: list[str] = Field(default_factory=list)
    # ALL levels, including those appearing only in elevations.
    levels: list[dict] = Field(default_factory=list)


class AxisMark(Contract):
    label: str
    direction: Literal['X', 'Y', 'Z']
    # A page-normalized point on the axis / level line, for the review overlay.
    point: list[float] = Field(min_length=2, max_length=2)
    evidence_ids: list[str] = Field(default_factory=list)


class View(Contract):
    id: str = Field(pattern=r'^[A-Za-z0-9_-]{1,120}$')
    page_id: str
    title: str
    kind: Literal['PLAN', 'ELEVATION', 'ISOMETRIC']
    bbox: list[float] = Field(min_length=4, max_length=4)
    title_bbox: list[float] = Field(min_length=4, max_length=4)
    visible_axes: list[Literal['X', 'Y', 'Z']] = Field(default_factory=list)
    fixed_axis: Literal['X', 'Y', 'Z'] | None = None
    fixed_label: str | None = None
    elevation_m: float | None = None
    elevation_basis: str = ''
    rotation_deg: float = 0
    mirrored: bool = False
    axis_marks: list[AxisMark] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    region_confirmed: bool = True
    structure_bbox: list[float] | None = Field(default=None,min_length=4,max_length=4)
    boundary_reason: str = ''
    boundary_evidence_ids: list[str] = Field(default_factory=list)
    localization: dict = Field(default_factory=dict)

    @model_validator(mode='after')
    def valid_box(self):
        for box in (self.bbox, self.title_bbox,*([self.structure_bbox] if self.structure_bbox is not None else [])):
            if not (0 <= box[0] < box[2] <= 1 and 0 <= box[1] < box[3] <= 1):
                raise ValueError('视图区域必须是有效的整页归一化矩形')
        if self.kind != 'ISOMETRIC' and self.visible_axes:
            if len(set(self.visible_axes)) != 2 or self.fixed_axis in self.visible_axes:
                raise ValueError('正投影视图需两个显示方向和一个独立固定方向')
        return self


class CoordinateRef(Contract):
    axis_label: str | None = None
    value_mm: float | None = None
    offset_mm: float = 0
    towards: str | None = None
    evidence_ids: list[str] = Field(default_factory=list)


class ElevationRef(Contract):
    value: float | None = None
    unit: Literal['m', 'mm'] = 'm'
    basis: Literal['TOS', 'BOB', 'CENTERLINE', 'REFERENCE'] = 'REFERENCE'
    offset_mm: float = 0
    # Explicitly choose the resulting reference; no automatic TOS -> centerline.
    reference_basis: Literal['TOS', 'BOB', 'CENTERLINE', 'REFERENCE'] = 'REFERENCE'
    evidence_ids: list[str] = Field(default_factory=list)


class PointRef(Contract):
    x: CoordinateRef = Field(default_factory=CoordinateRef)
    y: CoordinateRef = Field(default_factory=CoordinateRef)
    z: ElevationRef = Field(default_factory=ElevationRef)


class Boundary(Contract):
    kind: Literal['END', 'SPLICE', 'PROFILE_CHANGE', 'CONTINUOUS', 'UNKNOWN'] = 'UNKNOWN'
    evidence_ids: list[str] = Field(default_factory=list)
    note: str = ''


class Observation(Contract):
    id: str
    view_id: str
    profile: str = ''
    material: str = 'UNKNOWN'
    role: str = ''
    # Original page coordinates, independent of the crop and preview size.
    image_start: list[float] | None = Field(default=None, min_length=2, max_length=2)
    image_end: list[float] | None = Field(default=None, min_length=2, max_length=2)
    start: PointRef = Field(default_factory=PointRef)
    end: PointRef = Field(default_factory=PointRef)
    start_boundary: Boundary = Field(default_factory=Boundary)
    end_boundary: Boundary = Field(default_factory=Boundary)
    evidence_ids: list[str] = Field(default_factory=list)
    # Separate coincident assemblies must use different identities.
    assembly_identity: str = ''
    segment_identity: str = ''
    reference_line: str = 'AXIS'
    deferred: bool = False
    notes: str = ''


class LengthCandidate(Contract):
    observation_id: str
    profile: str
    component_count: int = 1
    start_mm: list[float] | None = None
    end_mm: list[float] | None = None
    reference_basis: str = ''
    semantic_key: str = ''
    length_mm: float | None = None
    formula: str = ''
    evidence_ids: list[str] = Field(default_factory=list)
    issues: list[str] = Field(default_factory=list)
    confirmed: bool = False
    normalizations: list[dict] = Field(default_factory=list)


class Member(Contract):
    id: str
    assembly_id: str
    component_index: int
    profile: str
    material: str
    start_mm: list[float]
    end_mm: list[float]
    length_mm: float
    observation_ids: list[str]
    evidence_ids: list[str]


class StageOne(Contract):
    views: list[View]
    frame: CoordinateFrame = Field(default_factory=CoordinateFrame)
    observations: list[Observation] = Field(default_factory=list)
    # Users can attach their own evidence; provenance is retained by the store.
    manual_evidence: list[Evidence] = Field(default_factory=list)
    profile_catalog: list[str] = Field(default_factory=list)
    discovery_inventory: dict = Field(default_factory=dict)
    analysis_pending: bool = False
    warnings: list[str] = Field(default_factory=list)
    notes: str = ''


class StageTwo(StageOne):
    candidates: list[LengthCandidate] = Field(default_factory=list)
    length_catalog: list[dict] = Field(default_factory=list)
    frame_issues: list[str] = Field(default_factory=list)


class MatchDecision(Contract):
    left: str
    right: str
    action: Literal['SAME', 'DISTINCT', 'DEFER']
    reason: str = Field(min_length=1)


class StageThree(Contract):
    decisions: list[MatchDecision] = Field(default_factory=list)
    matches: list[dict] = Field(default_factory=list)
    conflicts: list[dict] = Field(default_factory=list)
    members: list[Member] = Field(default_factory=list)
    exceptions: list[dict] = Field(default_factory=list)
    summary: list[dict] = Field(default_factory=list)


class StageEdit(Contract):
    revision: int = Field(ge=1)
    data: dict
    reason: str = Field(min_length=1)


class StageAction(Contract):
    revision: int = Field(ge=1)
    acknowledge_pending: bool = False
    settings: dict[str, str] = Field(default_factory=dict)
    reuse_saved: bool = True
    reason: str = ''
