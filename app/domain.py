"""Contracts for the active page catalog and per-view recognition workflow."""
from typing import Literal
from pydantic import ConfigDict, Field, model_validator
from .domain_base import Contract
from .quantity_reference import QuantityReference



class Evidence(Contract):
    id: str
    page_id: str
    kind: Literal['TEXT', 'VECTOR', 'MANUAL']
    bbox: list[float] = Field(min_length=4, max_length=4)
    text: str = ''
    points: list[list[float]] = Field(default_factory=list)
    style: dict = Field(default_factory=dict)



class View(Contract):
    model_config = ConfigDict(extra="ignore")
    id: str = Field(pattern=r'^[A-Za-z0-9_-]{1,120}$')
    page_id: str
    title: str
    kind: Literal['PLAN', 'ELEVATION', 'ISOMETRIC']
    bbox: list[float] = Field(min_length=4, max_length=4)
    title_bbox: list[float] = Field(min_length=4, max_length=4)
    fixed_label: str | None = None
    elevation_m: float | None = None
    elevation_basis: str = ''
    evidence_ids: list[str] = Field(default_factory=list)
    region_confirmed: bool = True
    structure_bbox: list[float] | None = Field(default=None,min_length=4,max_length=4)
    boundary_reason: str = ''
    boundary_evidence_ids: list[str] = Field(default_factory=list)
    localization: dict = Field(default_factory=dict)
    image_file: str = ''
    image_bbox: list[float] = Field(default_factory=list)
    detail_files: list[str] = Field(default_factory=list)
    colorized_image_file: str = ''
    colorized_detail_files: list[str] = Field(default_factory=list)
    colorized_label_audit_file: str = ''
    axis_labels: list[str] = Field(default_factory=list,description='当前视图的轴线或面名称：Alignment立面与EL标高平面')
    axes_reviewed: bool = False
    plane_name: str = ''
    steel_types: list[str] = Field(default_factory=list)
    takeoff_role: Literal['MEMBERS','REFERENCE','EXCLUDE','REVIEW'] = 'MEMBERS'
    filter_reason: str = ''
    filter_reviewed: bool = False
    quantity_reference: QuantityReference | None = None
    quantity_reference_image: str = ''

    @model_validator(mode='after')
    def valid_box(self):
        for box in (self.bbox, self.title_bbox,*([self.structure_bbox] if self.structure_bbox is not None else [])):
            if not (0 <= box[0] < box[2] <= 1 and 0 <= box[1] < box[3] <= 1):
                raise ValueError('视图区域必须是有效的整页归一化矩形')
        return self



class StageOne(Contract):
    model_config = ConfigDict(extra="ignore")
    workflow: Literal['image_first'] = 'image_first'
    catalog_mode: Literal['ai_pages'] = 'ai_pages'
    page_decisions: list[dict] = Field(default_factory=list)
    catalog_summary: dict = Field(default_factory=dict)
    axis_catalog: dict = Field(default_factory=dict)
    views: list[View]
    profile_catalog: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    notes: str = ''



class StageTwo(StageOne):
    recognition_mode: Literal['view_ledger'] = 'view_ledger'
    recognition_strategy: Literal['grid_agents'] | None = None
    grid_configuration: dict = Field(default_factory=dict)
    view_members: list[dict] = Field(default_factory=list)
    view_analysis: list[dict] = Field(default_factory=list)
    length_catalog: list[dict] = Field(default_factory=list)



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
    auto_run: bool | None = None
