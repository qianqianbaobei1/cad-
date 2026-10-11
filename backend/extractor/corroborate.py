# -*- coding: utf-8 -*-
"""用图纸自带的原生文字给模型结论做独立交叉验证。

问题：整条流水线的"证据"都来自同一个模型的自述——模型说断路器是 C16A，程序把它登记成
一条 TEXT 证据，检查器再按"TEXT 证据允许"放行。这是自己给自己作证，转一圈等于没验证。

独立来源在两个地方现成可用，成本为零：
1. DWG/DXF 上传时，CAD 原生矢量文字（cad.py 已解析并落盘为 *_cad_texts.json）；
2. PDF 带文字层时，页面原生文本（国内设计院多数转曲，所以这层常常是空的）。

本模块把模型给出的纯事实字段（箱号、回路编号、断路器、电缆）拿去原生文字里核对：
- 查得到 → 该字段的证据升级为 cad_native/ocr 来源，可判 CONFIRMED；
- 查不到 → 保持 PARSED_OK 并列入未验证清单，如实告诉人工“没有第二个来源可交叉验证”。

原生文字为空时明确返回 available=False，绝不把"没法验证"说成"验证通过"。
"""
from __future__ import annotations

import re
from typing import Any, Iterable

from .schema import (
    ExtractionResult,
    GroundedField,
    PHYSICAL_EVIDENCE_ORIGINS,
    ReviewStatus,
)

# 纯事实字段：与 schema.FIELD_EVIDENCE_POLICY 中不接受任何推断的字段保持一致
FACT_FIELD_PATHS = (
    "box.code",
    "box.location",
    "box.ip_rating",
    "circuit.circuit_no",
    "circuit.breaker",
    "circuit.cable",
)

_NOISE = re.compile(r"[\s\-_]+")


def normalize_for_match(value: Any) -> str:
    """归一化到可比形态：去空白、连字符、下划线，统一乘号与大小写。

    图纸上 "MCB-63 C16A/1P" 与 "MCB63C16A/1P" 是同一个事实，直接字符串相等会误判为不一致。
    """
    text = str(value or "").upper()
    text = text.replace("×", "X").replace("＊", "*").replace("，", ",")
    return _NOISE.sub("", text)


_SCHEDULE_HEADERS = ("设备表", "材料表", "回路表", "电缆表", "元器件表")


def schedule_lines(records: Iterable[Any], y_tol: float | None = None) -> list[str]:
    """把带坐标的设备表、材料表按行拼回去。

    只处理标题里写明是表的那一块。系统图上散落的回路文字保持原样，
    避免把左右两台箱子的字拼成一行。
    """
    items: list[dict[str, Any]] = []
    for record in records or []:
        if not isinstance(record, dict):
            continue
        text = str(record.get("text") or "").strip()
        if not text or "x" not in record or "y" not in record:
            continue
        try:
            x = float(record["x"])
            y = float(record["y"])
        except (TypeError, ValueError):
            continue
        try:
            height = abs(float(record.get("height") or record.get("h") or 0))
        except (TypeError, ValueError):
            height = 0.0
        items.append({"text": text, "x": x, "y": y, "height": height})
    if len(items) < 2:
        return []
    heights = sorted(item["height"] for item in items if item["height"] > 0)
    tol = y_tol
    if tol is None:
        tol = heights[len(heights) // 2] * 0.6 if heights else 8.0
    if tol <= 0:
        tol = 8.0
    items.sort(key=lambda item: (-item["y"], item["x"]))
    rows: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    current_y: float | None = None
    for item in items:
        if current_y is None or abs(item["y"] - current_y) <= tol:
            current.append(item)
            if current_y is None:
                current_y = item["y"]
        else:
            rows.append(current)
            current = [item]
            current_y = item["y"]
    if current:
        rows.append(current)

    def _blob(row: list[dict[str, Any]]) -> str:
        return "".join(cell["text"] for cell in row)

    header_at = None
    for index, row in enumerate(rows):
        blob = _blob(row)
        if any(hint in blob for hint in _SCHEDULE_HEADERS):
            header_at = index
            break
    if header_at is None:
        return []
    chosen = [rows[header_at]]
    previous_y = max(cell["y"] for cell in rows[header_at])
    for row in rows[header_at + 1:]:
        row_y = max(cell["y"] for cell in row)
        if previous_y - row_y > tol * 6:
            break
        chosen.append(row)
        previous_y = row_y
    lines: list[str] = []
    for row in chosen:
        ordered = sorted(row, key=lambda cell: cell["x"])
        lines.append(" ".join(cell["text"] for cell in ordered))
    return lines


def build_native_corpus(sources: Iterable[Any]) -> tuple[str, int]:
    """把原生文字来源拼成一个大写归一化语料，返回 (语料, 条目数)。

    sources 支持两种形态：
    - 字符串列表（PDF 文字层逐行、CAD 文字逐条都可）；
    - [{"text": ...}, ...] 之类的字典列表。
    """
    parts: list[str] = []
    for item in sources or []:
        if isinstance(item, dict):
            text = item.get("text") or ""
        else:
            text = str(item or "")
        if text:
            parts.append(normalize_for_match(text))
    return "".join(parts), len(parts)


def _claims_of(item: Any) -> dict[str, GroundedField]:
    claims = getattr(item, "claims", None)
    return claims if isinstance(claims, dict) else {}


def corroborate(result: ExtractionResult, native_sources: Iterable[Any],
                source: str = "cad_native") -> dict:
    """用原生文字核对纯事实字段，就地更新证据来源与核验状态。

    返回统计结果，可直接放进任务摘要供前端展示：
    available=False 表示这张图没有可用的原生文字，交叉验证没有发生。
    """
    if source not in PHYSICAL_EVIDENCE_ORIGINS:
        raise ValueError(f"未知的原生证据来源: {source}")

    corpus, entry_count = build_native_corpus(native_sources)
    store = result.evidence_store or {}
    stats: dict[str, Any] = {
        "available": bool(corpus),
        "source": source,
        "native_entries": entry_count,
        "values_checked": 0,
        "corroborated": 0,
        "unverified": 0,
        "coverage_rate": 0.0,
        "unverified_examples": [],
    }
    if not corpus:
        return stats

    items = list(result.boxes or []) + list(result.circuits or [])
    for item in items:
        for field_path, claim in _claims_of(item).items():
            if field_path not in FACT_FIELD_PATHS:
                continue
            raw = normalize_for_match(claim.raw_value or claim.value)
            if not raw:
                continue
            stats["values_checked"] += 1
            if raw in corpus:
                stats["corroborated"] += 1
                claim.review_status = ReviewStatus.CONFIRMED.value
                claim.notes = (claim.notes + "；" if claim.notes else "") + f"{source} 原生文字命中"
                for ev_id in claim.value_evidence_ids:
                    evidence = store.get(ev_id)
                    if evidence is not None:
                        evidence.origin = source
            else:
                stats["unverified"] += 1
                owner = getattr(item, "code", "") or getattr(item, "box", "")
                label = f"{owner} {field_path.split('.')[-1]}={claim.raw_value}"
                if len(stats["unverified_examples"]) < 12:
                    stats["unverified_examples"].append(label)

    if stats["values_checked"]:
        stats["coverage_rate"] = round(stats["corroborated"] / stats["values_checked"], 4)
    return stats


def corroboration_issues(stats: dict, severity: str = "WARNING") -> list[dict]:
    """把交叉验证结果转成待核对条目（未命中才报，命中不打扰人工）。"""
    issues = []
    if not stats.get("available"):
        issues.append({
            "location": "交叉验证",
            "detail": ("本图没有可用的原生文字（文字已转曲或未提供矢量文字层），"
                       "模型结论没有第二个来源交叉验证，关键字段需人工抽查"),
            "severity": "INFO",
        })
    else:
        unverified = stats.get("unverified") or 0
        if stats.get("values_checked") and unverified:
            examples = "、".join(stats.get("unverified_examples") or [])
            issues.append({
                "location": "交叉验证",
                "detail": (f"{stats['values_checked']} 个关键字段里 {unverified} 个未在图纸原生文字中命中"
                           f"（命中率 {stats['coverage_rate']:.1%}）：{examples}。"
                           f"未命中不等于错误（可能是转曲、分块边界或原生文字缺失），请在图纸上逐条核对"),
                "severity": severity,
            })
    if stats.get("thin_text"):
        issues.append({
            "location": "交叉验证",
            "detail": "图元多、文字少，疑似文字转曲。文字层里对得上的字段不能当成整张配电箱图已经核对通过",
            "severity": "WARNING",
        })
    return issues
