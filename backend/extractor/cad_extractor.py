# -*- coding: utf-8 -*-
"""CAD (DXF/DWG) 原生几何解析器。

从可读取的 CAD 文字与几何实体生成箱体和回路候选。实体内容、块结构、比例、图层与
布局空间可能不完整或无法解释；输出不等同于完整图纸事实，必须经过独立证据、覆盖
与人工核验，不能承诺 100% 准确或零遗漏。
"""

from collections import defaultdict
import re

from .cad_ledger import source_key
from .schema import (
    BBox, Evidence, EvidenceType, GroundedField,
    ReviewStatus,
)

from .config import domain as _domain, pipeline as _pipeline

_B = _domain()["breaker"]
_CAD_CONFIG = _pipeline()["cad"]
_V = _domain()["vocabulary"]
_D = _domain()["cad"]

RE_CIRCUIT_NO = re.compile(_V["circuit_no_regex"], re.I)
RE_PHASE = re.compile(_V["phase_regex"], re.I)
RE_POWER = re.compile(_V["power_regex"], re.I)
_CABLE_CFG = _domain().get("cable") or {}
_CABLE_FAMILY_RES = [
    re.compile(pattern, re.I) for pattern in _CABLE_CFG.get("family_patterns", [])
]
_LAYING_RE = re.compile(_CABLE_CFG.get("laying_method_regex") or r"$^", re.I)
_SECTION_RE = re.compile(r"\d+\s*[xX×*]\s*\d+")
_AMP_SEG_RE = re.compile(r"^\d+(?:\.\d+)?A$", re.I)
_CODE_TOKEN_RE = re.compile(
    r"(?<![A-Za-z0-9\-])([A-Za-z0-9]+(?:[\-/][A-Za-z0-9]+)*)(?![A-Za-z0-9])"
)

BREAKER_PREFIXES = _B["model_prefixes"]
RE_BREAKER = re.compile(_B["model_regex_template"].format(prefixes=BREAKER_PREFIXES), re.I)
RE_BREAKER_FALLBACK = re.compile(_B["fallback_regex"], re.I)
RE_DISALLOWED_PREFIXES = re.compile(_B["disallowed_prefixes_regex"], re.I)

RE_ENGINEERING_UNIT = re.compile(_V["engineering_unit_regex"], re.I)
RE_NATIONAL_ATLAS = re.compile(_V["national_atlas_regex"], re.I)
CIRCUIT_NO_MAX_DIGITS = int(_V["circuit_no_max_digits"])
# 变频柜出线 D01、控制线 D01-C1、风机就地按钮 K1-1、负荷 P(S)-1。
# 不写进通用回路号正则，避免把 C01 这种柜号收成回路。
_BRANCH_CIRCUIT_NO = re.compile(r"^(?:D\d{2}(?:-C\d+)?|K\d+-\d+|P\(S\)-\d+)$", re.I)

# 电压/频率/功率这类工程量单独成文时不是箱号，按配置正则排除（可按项目补规则，不改代码）
NON_PANEL_TOKEN_RES = [re.compile(pattern, re.I) for pattern in _V.get("non_panel_token_regexes", [])]
_PANEL_FAMILY_SUFFIXES = frozenset(
    part.upper()
    for part in re.findall(r"[A-Za-z]{2,}", str((_domain().get("catalog") or {}).get("panel_prefix_pattern") or ""))
)


_SPD_FAMILY_RES = [re.compile(pattern, re.I) for pattern in _D.get("spd_family_patterns", [])]
_SPD_CLASS_RE = re.compile(_D.get("spd_class_regex") or r"$^")


def _is_spd_callout(text: str) -> bool:
    """浪涌型号或「Ⅱ级-4P」这种整段极数标是保护器。试验说明句不是。

    加号后面是前一台器件的附件。附件里出现浪涌系列，不把整段当成另一只保护器。
    """
    raw = (text or "").strip()
    if not raw or any(mark in raw for mark in ("。", "；", "，")):
        return False
    if _SPD_CLASS_RE.match(raw):
        return True
    if len(raw) > 32:
        return False
    head = re.split(r"[+＋]", raw, maxsplit=1)[0]
    return any(pattern.search(head) for pattern in _SPD_FAMILY_RES)


def _looks_like_cable(text: str) -> bool:
    """电缆由线缆系列词法或「截面 × 敷设」结构判定，不单独维护第二份型号表。"""
    raw = text or ""
    if any(pattern.search(raw) for pattern in _CABLE_FAMILY_RES):
        return True
    return bool(_SECTION_RE.search(raw) and _LAYING_RE.search(raw))


def _token_is_device_rating(token: str) -> bool:
    """分段里出现额定电流（如 63A）的是器件规格，不是箱号。"""
    parts = [part for part in re.split(r"[-/]", token) if part]
    return any(_AMP_SEG_RE.match(part) for part in parts)


def _text_is_circuit_anchor(text: str) -> bool:
    raw = (text or "").strip()
    return bool(RE_CIRCUIT_NO.match(raw) or _BRANCH_CIRCUIT_NO.match(raw))


def _token_rejected(token: str) -> bool:
    if not re.search(r"[A-Za-z]", token):
        return True
    # 没有数字的代号只在「字母段 + 箱种后缀」这种形状下成立，例如 XFDT-AT。
    if not re.search(r"\d", token) and not _has_panel_shape(token):
        return True
    if re.search(rf"\d{{{CIRCUIT_NO_MAX_DIGITS},}}", token):
        return True
    if RE_NATIONAL_ATLAS.match(token) or RE_ENGINEERING_UNIT.match(token):
        return True
    if RE_PHASE.match(token) or _text_is_circuit_anchor(token):
        return True
    parts = [part for part in re.split(r"[-/]", token) if part]
    if any(pattern.match(token) or any(pattern.match(part) for part in parts) for pattern in NON_PANEL_TOKEN_RES):
        return True
    if re.search(r"\d+[Xx×]\d+", token) or _looks_like_cable(token):
        return True
    if re.search(r"(?:SC|PC|KBG|JDG|MT)\d", token, re.I):
        return True
    if RE_BREAKER.match(token) or re.fullmatch(r"[CDB]\d{1,3}A", token, re.I):
        return True
    if _token_is_device_rating(token):
        return True
    if RE_DISALLOWED_PREFIXES.match(token) or any(RE_DISALLOWED_PREFIXES.match(part) for part in parts):
        return True
    if not _has_panel_shape(token):
        return True
    return False


def _segment_is_panel_core(part: str) -> bool:
    """一段里要有至少两个字母再夹着数字，或单字母加正好两位数字（C01）。

    单字母加三位以上（A116）和两位字母加三位以上（PK306）是房间号。
    """
    if re.fullmatch(r"[A-Z]\d{2}", part, re.I):
        return True
    if re.fullmatch(r"[A-Z]\d{3,}", part, re.I) or re.fullmatch(r"[A-Z]{2}\d{3,}", part, re.I):
        return False
    return bool(re.search(r"[A-Z]{2,}", part, re.I) and re.search(r"\d", part))


def _has_panel_shape(token: str) -> bool:
    """1X、40R、65H2、TMY-4X 没有这样的段。B1ATPY1、10RDAL、AW1/2/3/4-CDZ 有。

    纯字母也可以：一段是配置里的箱种后缀（AT、AL），另一段至少三个字母（XFDT-AT）。
    """
    parts = [part for part in re.split(r"[-/]", token) if part]
    if any(_segment_is_panel_core(part) for part in parts):
        return True
    letter_parts = [part for part in parts if re.fullmatch(r"[A-Z]{2,}", part, re.I)]
    if len(letter_parts) < 2:
        return False
    return any(
        part.upper() in _PANEL_FAMILY_SUFFIXES
        and any(other.upper() not in _PANEL_FAMILY_SUFFIXES and len(other) >= 3 for other in letter_parts)
        for part in letter_parts
    )


def iter_panel_codes(text: str) -> list[str]:
    """按统一词法列出文本中的箱号。电缆、穿管、回路号、额定电流都不会出现在结果里。"""
    clean = re.split(r"[:：]", text or "", maxsplit=1)[0].strip()
    clean = re.sub(r"^消防", "", clean).strip()
    found: list[str] = []
    seen: set[str] = set()
    for match in _CODE_TOKEN_RE.finditer(clean):
        token = match.group(1).strip()
        if _token_rejected(token):
            continue
        code = token.upper()
        if code not in seen:
            seen.add(code)
            found.append(code)
    return found


def extract_panel_code(text: str) -> str | None:
    """通用电气配电箱/柜体编号提取器（无项目/图纸特定硬编码）。"""
    codes = iter_panel_codes(text)
    return codes[0] if codes else None


def _exact_feed_code(text: str) -> str | None:
    """整段就是箱号，或「箱号:回路号」。说明文字里顺带出现的编号不算配出对象。"""
    head = re.split(r"[:：]", text or "", maxsplit=1)[0].strip()
    code = extract_panel_code(head)
    if not code:
        return None
    if re.sub(r"\s+", "", head).upper() != code:
        return None
    return code


def _is_breaker_legend(text: str) -> bool:
    """「图中 MCCB…表示…」是符号说明，不是这台箱上的开关。"""
    marks = ((_domain().get("cad") or {}).get("breaker_legend_marks") or ())
    raw = text or ""
    return any(mark and mark in raw for mark in marks)


def _text_is_breaker(text: str) -> bool:
    """整段就是箱号时不是开关。B1-AL2 这类编号开头的字母数字不是极数。"""
    if not text or _looks_like_cable(text) or _exact_feed_code(text) or _is_breaker_legend(text):
        return False
    if re.fullmatch(r"[CDB]\d{1,3}类", text.strip()):
        return False
    if re.fullmatch(r"\d+[xX×]\d+(?:\.\d+)?A", text.strip(), re.I):
        return True
    return bool(RE_BREAKER.search(text) or RE_BREAKER_FALLBACK.search(text))


_ROW_DEVICE_RES = {
    field: [re.compile(pattern, re.I) for pattern in patterns]
    for field, patterns in (_D.get("row_device_families") or {}).items()
    if field in {"contactor", "ct", "thermal"}
}
_THERMAL_SETTING_RE = re.compile(_D.get("thermal_setting_regex") or r"$^")


def _row_device_field(text: str) -> str | None:
    """同一行上的接触器、热继电器、互感器型号归到对应栏，不写进负荷名。"""
    raw = (text or "").strip()
    if not raw or len(raw) > 32 or any(mark in raw for mark in ("。", "；", "，")):
        return None
    if _text_is_breaker(raw) or _looks_like_cable(raw) or _is_spd_callout(raw):
        return None
    for field, patterns in _ROW_DEVICE_RES.items():
        if any(pattern.search(raw) for pattern in patterns):
            return field
    return None


def assign_preview_bboxes(boxes: list, circuits: list, cad_texts: list | None,
                          page_by_box: dict[str, int] | None = None,
                          page_rects: dict[int, tuple[float, float, float, float]] | None = None) -> None:
    """用 CAD 文字的模型坐标给箱柜、回路打页面归一化高亮框。

    page_rects 是每页对应的图框模型坐标（预览一页 = 一张完整图幅），换算是精确的；
    没有时才退回按本页文字范围估算。同一张图上几台箱都有 N1、N2，回路号只取离本箱箱号
    最近的那一处；箱柜框 = 箱号 + 本箱各回路位置的合并范围。
    """
    pad = float(_CAD_CONFIG.get("preview_bbox_pad", 0.015))
    min_size = float(_CAD_CONFIG.get("preview_bbox_min", 0.04))
    fallback = _CAD_CONFIG.get("preview_bbox_fallback") or [0.05, 0.05, 0.9, 0.9]
    fb_x, fb_y, fb_w, fb_h = (float(v) for v in fallback)
    preferred = page_by_box or {}

    by_page: dict[int, list] = defaultdict(list)
    for item in cad_texts or []:
        if item.get("page") is None or item.get("x") is None or item.get("y") is None:
            continue
        by_page[int(item["page"])].append(item)

    bounds: dict[int, tuple[float, float, float, float]] = {}
    for page, items in by_page.items():
        if page_rects and page in page_rects:
            x0, y0, x1, y1 = (float(v) for v in page_rects[page])
            bounds[page] = (x0, x1, y0, y1)
        else:
            xs = [float(t["x"]) for t in items]
            ys = [float(t["y"]) for t in items]
            bounds[page] = (min(xs), max(xs), min(ys), max(ys))

    def _fallback(page: int) -> BBox:
        return BBox(x=fb_x, y=fb_y, w=fb_w, h=fb_h, page=max(1, page))

    def _rect(page: int, pts: list[tuple[float, float]]) -> BBox | None:
        if page not in bounds or not pts:
            return None
        minx, maxx, miny, maxy = bounds[page]
        spanx = max(maxx - minx, 1.0)
        spany = max(maxy - miny, 1.0)
        xs = [(px - minx) / spanx for px, _ in pts]
        ys = [(maxy - py) / spany for _, py in pts]
        x0, x1 = max(0.0, min(xs)), min(1.0, max(xs))
        y0, y1 = max(0.0, min(ys)), min(1.0, max(ys))
        x = max(0.0, x0 - pad)
        y = max(0.0, y0 - pad)
        w = min(1.0 - x, max(x1 - x0, 0.0) + 2 * pad)
        h = min(1.0 - y, max(y1 - y0, 0.0) + 2 * pad)
        if w < min_size:
            x = max(0.0, min(x, 1.0 - min_size))
            w = min(min_size, 1.0 - x)
        if h < min_size:
            y = max(0.0, min(y, 1.0 - min_size))
            h = min(min_size, 1.0 - y)
        if w <= 0 or h <= 0:
            return None
        return BBox(x=round(x, 4), y=round(y, 4), w=round(min(w, 1.0), 4), h=round(min(h, 1.0), 4), page=page)

    def _hits(page: int, needle: str) -> list:
        target = (needle or "").strip().upper()
        if not target:
            return []
        return [t for t in by_page.get(page, []) if str(t.get("text") or "").strip().upper() == target]

    def _nearest(items: list, anchor: tuple[float, float] | None):
        if not items:
            return None
        if anchor is None:
            return items[0]
        return min(items, key=lambda t: (float(t["x"]) - anchor[0]) ** 2 + (float(t["y"]) - anchor[1]) ** 2)

    circuits_by_box: dict[str, list] = defaultdict(list)
    for circuit in circuits:
        circuits_by_box[(circuit.box or "").strip()].append(circuit)

    for box in boxes:
        code = (box.code or "").strip()
        pages = sorted({int(t["page"]) for t in (cad_texts or [])
                        if str(t.get("text") or "").strip().upper() == code.upper() and t.get("page")})
        page = preferred.get(code) or (pages[0] if pages else 1)
        if pages and page not in pages:
            page = max(pages, key=lambda p: len(_hits(p, code)))
        code_hits = _hits(page, code)
        anchor_t = code_hits[0] if code_hits else None
        anchor = (float(anchor_t["x"]), float(anchor_t["y"])) if anchor_t else None
        box_pts = [anchor] if anchor else []
        for circuit in circuits_by_box.get(code, []):
            number = (circuit.circuit_no or "").strip()
            hit = None if number in ("", "进线") else _nearest(_hits(page, number), anchor)
            if hit is not None:
                pt = (float(hit["x"]), float(hit["y"]))
                box_pts.append(pt)
                circuit.bbox = _rect(page, [pt])
            else:
                circuit.bbox = None
        box.bbox = _rect(page, box_pts) or _fallback(page)
        for circuit in circuits_by_box.get(code, []):
            if circuit.bbox is None:
                circuit.bbox = box.bbox

    known = {(b.code or "").strip() for b in boxes}
    for code, items in circuits_by_box.items():
        if code in known:
            continue
        for circuit in items:
            circuit.bbox = _fallback(int(preferred.get(code) or 1))


def _native_claim(store: dict, value: str, source: dict, field_path: str) -> GroundedField:
    """把一个字段指回写出它的那条 CAD 文字。没有 handle 时不假装已经对上实体。"""
    evidence_ids: list[str] = []
    if value and source and source.get("handle"):
        evidence_id = "cad." + source_key(source).replace("|", ".")
        if evidence_id not in store:
            store[evidence_id] = Evidence(
                evidence_id=evidence_id,
                evidence_type=EvidenceType.TEXT.value,
                raw_content=value,
                origin="cad_native",
                file_sha256=str(source.get("file_sha256") or ""),
                handle=str(source.get("handle") or ""),
                owner=str(source.get("owner") or ""),
                insert_path=str(source.get("insert_path") or ""),
                layout=str(source.get("layout") or "Model"),
            )
        evidence_ids.append(evidence_id)
    return GroundedField(
        value=value,
        raw_value=value,
        value_evidence_ids=evidence_ids,
        confidence=None,
        review_status=ReviewStatus.PARSED_OK.value if evidence_ids else ReviewStatus.UNASSESSED.value,
    )
