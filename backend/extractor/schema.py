# -*- coding: utf-8 -*-
"""Model observations and the assembled quotation have separate schemas."""
from __future__ import annotations
import re
from typing import List, Optional
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

PROMPT_VERSION = "3.0"
CONTRACT_VERSION = "3.0"


def normalize_code(value: str) -> str:
    return re.sub(r"\s+", "", value).upper()


class BBox(BaseModel):
    """图纸页内的归一化坐标(0-1)，用于图-表联动与核对定位。

    模型给不出位置时整个字段为 None，程序不猜坐标。多页图纸靠 page 指明是哪一页，
    否则前端只能默认第 1 页，横向图纸多的项目定位就全错。
    """

    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)
    w: float = Field(gt=0, le=1)
    h: float = Field(gt=0, le=1)
    page: int = Field(1, ge=1)

    @model_validator(mode="after")
    def clip_out_of_page(self):
        """坐标越界时裁进页面内；裁无可裁则视为未定位。"""
        if self.x + self.w > 1:
            self.w = round(1 - self.x, 4)
        if self.y + self.h > 1:
            self.h = round(1 - self.y, 4)
        return self

    @property
    def usable(self) -> bool:
        return self.w >= 0.01 and self.h >= 0.01


class Uncertainty(BaseModel):
    """待人工核对项。

    source 区分来源，是修正历史数据的关键标记：
    - "model"：模型在图纸上看到但拿不准的事实，修完图纸才会变；
    - "program"：assemble/checker 根据当前数据算出来的告警，数据一变就应重算；
    - ""：旧导出遗留，来源不明。
    导出到 Excel 时两类分开放，避免下次读回时把程序告警当成模型事实。
    """

    location: str = Field("", description="位置，如 WL3 回路 / 箱体图注")
    detail: str = Field("", description="具体问题")
    bbox: Optional[BBox] = Field(None, description="图纸上的位置，定位不到留空")
    resolved: bool = Field(False, description="用户是否已确认")
    source: str = Field("", description="model / program / 空")

    @classmethod
    def from_text(cls, text: str) -> "Uncertainty":
        """程序生成的告警只有一行文本，按“位置：问题”拆开，供界面分栏显示。

        旧 Excel 回读时已确认项带有"（已确认）"前缀（见 extractor/excel.py 的导出
        写法）：识别并剥离该前缀，同时恢复 resolved=True，避免确认标记丢失。
        """
        text = text.strip()
        resolved = False
        if text.startswith("（已确认）"):
            resolved = True
            text = text[len("（已确认）"):].strip()
        head, sep, tail = text.partition("：")
        if sep and len(head) <= 40:
            return cls(location=head.strip(), detail=tail.strip(), resolved=resolved)
        return cls(location="", detail=text, resolved=resolved)

    @property
    def text(self) -> str:
        """退回“位置：问题”的单行写法，Excel 与旧接口沿用。"""
        if self.location and self.detail:
            return f"{self.location}：{self.detail}"
        return self.location or self.detail

    def __str__(self) -> str:
        return self.text


class Box(BaseModel):
    code: str = Field("", description="设备编号,如 2ALE / CD1")
    name: str = Field("", description="设备名称,如 应急照明配电箱")
    ip_rating: str = Field("", description="防护等级,如 IP30")
    install: str = Field("", description="安装方式,如 底边距地1.5m明装")
    location: str = Field("", description="安装位置")
    size: str = Field("", description="参考尺寸")
    quantity: int = Field(1, description="数量(台)")
    note: str = Field("", description="备注")

    @field_validator("code")
    @classmethod
    def clean_code(cls, value: str) -> str:
        return normalize_code(value)


class Circuit(BaseModel):
    box: str = Field("", description="所属配电箱编号")
    phase: str = Field("", description="相序,如 L1/L2/L3/L123")
    breaker: str = Field("", description="空气开关/断路器型号")
    contactor: str = Field("", description="交流接触器")
    ct: str = Field("", description="电流互感器")
    thermal: str = Field("", description="热继电器")
    power_kw: str = Field("", description="设备容量kW")
    circuit_no: str = Field("", description="回路编号")
    cable: str = Field("", description="导线型号及敷设")
    current_a: str = Field("", description="计算电流A")
    load_name: str = Field("", description="回路名称/用电设备")
    secondary_ref: str = Field("", description="二次图编号")
    start_method: str = Field("", description="启动方式")
    note: str = Field("", description="备注")
    bbox: Optional[BBox] = Field(None, description="该回路在图纸页内的归一化位置，定位不到留空")

    @field_validator("box")
    @classmethod
    def clean_box(cls, value: str) -> str:
        return normalize_code(value)


class Component(BaseModel):
    name: str = Field("", description="元器件名称")
    spec: str = Field("", description="规格型号")
    unit: str = Field("", description="单位")
    quantity: float = Field(0, allow_inf_nan=False, description="数量")
    used_in: str = Field("", description="用于箱体/回路")
    note: str = Field("", description="备注")


class Requirement(BaseModel):
    item: str = Field("", description="项目")
    content: str = Field("", description="要求内容")


class ExtraDevice(BaseModel):
    """An observed device that is not represented by a circuit field."""
    name: str
    spec: str = ""
    unit: str = ""
    quantity: float = Field(gt=0, allow_inf_nan=False)
    used_in: str = ""
    note: str = ""


class RawExtraction(BaseModel):
    """Only facts observed by the model; no model-computed totals or title."""
    model_config = ConfigDict(extra="forbid")
    boxes: List[Box]
    circuits: List[Circuit]
    extra_devices: List[ExtraDevice]
    requirements: List[Requirement]
    uncertainties: List[Uncertainty]


class DistributionNode(BaseModel):
    """配电系统拓扑树节点：支持 项目 -> 一级总配电柜 -> 二级分配电箱 -> 一次出线回路 / 二次控制原理图。"""
    id: str = Field(..., description="唯一节点ID")
    code: str = Field("", description="设备/箱体/回路编号")
    name: str = Field("", description="设备名称或回路名称")
    node_type: str = Field("box", description="cabinet(一级总配电柜) / box(二级分配电箱) / circuit(出线支路) / secondary(二次控制原理图)")
    parent_code: str = Field("", description="上级供电设备编号")
    feed_circuit: str = Field("", description="上级供电出线回路编号")
    circuits_count: int = Field(0, description="下属回路总数")
    power_kw: str = Field("", description="设备容量或回路容量(kW)")
    secondary_ref: str = Field("", description="关联二次控制原理图编号")
    children: List[DistributionNode] = Field(default_factory=list, description="子节点")
    note: str = Field("", description="工程备注")


class AssembledMeta(BaseModel):
    model: str = ""
    prompt_version: str = PROMPT_VERSION
    contract_version: str = CONTRACT_VERSION


class ExtractionResult(BaseModel):
    title: str = Field("配电箱元器件清单(报价用)", description="清单标题")
    boxes: List[Box] = Field(default_factory=list)
    circuits: List[Circuit] = Field(default_factory=list)
    components: List[Component] = Field(default_factory=list)
    requirements: List[Requirement] = Field(default_factory=list)
    uncertainties: List[Uncertainty] = Field(default_factory=list, description="图纸字迹不清、需人工核对的项")
    topology: List[DistributionNode] = Field(default_factory=list, description="配电系统拓扑树")
    meta: AssembledMeta = Field(default_factory=AssembledMeta)

