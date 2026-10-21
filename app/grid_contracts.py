"""Compact grid-worker and coordinator output contracts."""
from typing import Literal
from pydantic import Field,field_validator
from .domain_base import Contract
from .member_recognition import ImageMember,LengthInput,normalize_view_tags


class GridCandidate(Contract):
    spec: str = Field(min_length=1,max_length=120)
    view_tags: list[str] = Field(max_length=1)
    label_point: tuple[float,float]
    extent: Literal['COMPLETE','CONTINUES','UNCERTAIN']
    length: LengthInput
    location: str = Field(max_length=4000)
    issue: str | None = Field(max_length=4000)

    @field_validator('label_point')
    @classmethod
    def in_image(cls,value):
        if not all(0<=v<=1 for v in value):raise ValueError('标签中心坐标必须在 [0,1]')
        return value

    @field_validator('view_tags')
    @classmethod
    def names_only(cls,value):return normalize_view_tags(value)


class GridChildResponse(Contract):
    candidates: list[GridCandidate]


class GridMember(ImageMember):
    member_cnt: Literal[1] = 1
    view_tags: list[str] = Field(max_length=1)
    candidate_ids: list[str]
    segmentation_status: Literal['CONFIRMED','UNRESOLVED']
    endpoints: list[str] | None = Field(min_length=2,max_length=2)
    original_label_point: tuple[float,float] | None = None

    @field_validator('original_label_point')
    @classmethod
    def in_image(cls,value):
        if value is not None and not all(0<=v<=1 for v in value):raise ValueError('原图标签坐标必须在 [0,1]')
        return value


class GridDecision(Contract):
    candidate_id: str
    action: Literal['INCLUDED','DUPLICATE','NOT_MEMBER','UNRESOLVED']
    member_index: int | None = Field(ge=0)
    reason: str = Field(min_length=1,max_length=2000)
    duplicate_of: str | None = None


class GridMasterResponse(Contract):
    members: list[GridMember]
    decisions: list[GridDecision]
