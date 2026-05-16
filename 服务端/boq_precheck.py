"""
BOQ 清单质量预检模块 — Stage 0
在正式推理前扫描整份清单，给出可信度预判。
"""
import re
import json
from dataclasses import dataclass, field

# ══════════════════════════════════════
# 外部依赖检测（见图纸/按设计要求/暂定等）
# ══════════════════════════════════════

SEE_DRAWING_PATTERNS = [
    (re.compile(r"详[见参].{0,6}图[纸示集]"), "详见图纸"),
    (re.compile(r"参[见照].{0,6}[图设]计"), "参照图纸/设计"),
    (re.compile(r"由[设建].{0,6}[确定认]"), "由设计确定"),
    (re.compile(r"[暂待].{0,4}[定报核]"), "暂定/待报"),
    (re.compile(r"按[设建].{0,4}[要求定]"), "按设计要求"),
    (re.compile(r"按[图集].{0,4}[选确]"), "按图集选用"),
    (re.compile(r"见图[纸集]"), "见图纸"),
    (re.compile(r"see\s*drawing", re.IGNORECASE), "see drawing"),
]

# 子串关系去重缓存 — 先检测到的长匹配覆盖短匹配
_SUBSUME_PAIRS = [
    ("详见图纸", "见图纸"),  # 详见图纸 包含 见图纸，保留前者
]

# 编码前缀 → 期望的名称关键词
CODE_NAME_CONSISTENCY = {
    "011101001": ["水泥砂浆", "砂浆"],
    "011101002": ["细石混凝土", "细石"],
    "011101003": ["自流平"],
    "011101004": ["耐磨"],
    "011101006": ["找平层", "找平"],
    "011102001": ["石材", "大理石", "花岗岩"],
    "011102002": ["拼碎石材", "拼碎"],
    "011102003": ["块料", "瓷砖", "地砖", "抛光砖", "玻化砖", "防滑砖", "瓷质砖", "釉面砖"],
    "011103001": ["橡塑板", "橡胶板", "塑胶板"],
    "011103002": ["橡塑卷材", "橡胶卷材", "PVC卷材", "塑胶卷材"],
    "011104001": ["地毯"],
    "011104002": ["木地板", "竹地板", "复合地板", "实木地板", "强化地板"],
    "011105001": ["水泥砂浆踢脚", "砂浆踢脚"],
    "011105002": ["石材踢脚"],
    "011105003": ["块料踢脚", "瓷砖踢脚"],
    "011105006": ["金属踢脚", "不锈钢踢脚", "铝合金踢脚"],
    "011106001": ["水泥砂浆楼梯", "砂浆楼梯"],
    "011106002": ["石材楼梯"],
    "011106003": ["块料楼梯", "瓷砖楼梯"],
    "011107001": ["水泥砂浆台阶", "砂浆台阶"],
    "011107002": ["石材台阶"],
    "011107004": ["块料台阶", "瓷砖台阶"],
}

# 关键字段缺失检测
REQUIRED_FIELDS = {
    "项目编码": r"(?:项目编码|清单编码|编码)[：:]\s*(\d{6,12})",
    "项目名称": r"(?:项目名称|名称)[：:]\s*(.+?)(?:\n|$)",
    "工程量": r"(?:工程量|数量)[：:]\s*([\d.]+)",
    "计量单位": r"(?:计量单位|单位)[：:]\s*(m³|m3|m²|m2|㎡|m|米|t|吨|kg|个|套|台|樘|m2|m²)",
}


@dataclass
class ExternalReference:
    """外部依赖记录"""
    pattern: str
    matched_text: str
    affected_fields: list[str]
    reference_type: str  # see_drawing / by_design / provisional / manual_required


@dataclass
class BoqPreCheckResult:
    """BOQ 质量预检结果"""
    overall_quality: str  # excellent / good / fair / poor
    quality_score: float  # 0.0 - 1.0

    # 基本信息
    boq_code: str
    boq_name: str
    has_quantity: bool
    has_feature_text: bool

    # 外部依赖
    has_external_ref: bool
    external_refs: list[dict] = field(default_factory=list)

    # 缺失字段
    missing_fields: list[str] = field(default_factory=list)

    # 编码-名称一致性
    code_name_match: bool = True
    code_name_detail: str = ""

    # 问题列表
    issues: list[dict] = field(default_factory=list)

    # 摘要
    summary: str = ""


def scan_boq_content(content: str) -> BoqPreCheckResult:
    """扫描清单文本，返回质量预检结果。"""
    if not content or not content.strip():
        return BoqPreCheckResult(
            overall_quality="poor", quality_score=0.0,
            boq_code="", boq_name="", has_quantity=False, has_feature_text=False,
            has_external_ref=False,
            missing_fields=["所有字段"],
            code_name_match=False,
            issues=[{"severity": "block", "type": "empty_input", "message": "输入为空"}],
            summary="输入为空，无法处理"
        )

    text = content.strip()
    issues: list[dict] = []
    external_refs: list[dict] = []
    missing_fields: list[str] = []

    # ── 1. 提取编码和名称 ──
    boq_code = ""
    boq_name = ""
    m = re.search(r"(?:项目编码|清单编码|编码)[：:]\s*(\d{6,12})", text)
    if m:
        boq_code = m.group(1).strip()
    else:
        # 尝试从文本中提取 9-12 位数字
        m = re.search(r"(?<!\d)(\d{9,12})(?!\d)", text)
        if m:
            boq_code = m.group(1)

    m = re.search(r"(?:项目名称|名称)[：:]\s*(.+?)(?:\n|$)", text)
    if m:
        boq_name = m.group(1).strip()
    else:
        # 尝试常见名称关键词
        common_names = [
            "水泥砂浆楼地面", "细石混凝土楼地面", "自流平楼地面", "耐磨楼地面",
            "块料楼地面", "石材楼地面", "拼碎石材楼地面",
            "橡塑板楼地面", "橡塑卷材楼地面",
            "地毯楼地面", "竹地板", "木地板", "复合地板",
            "水泥砂浆踢脚线", "石材踢脚线", "块料踢脚线", "金属踢脚线",
            "水泥砂浆楼梯", "石材楼梯", "块料楼梯",
            "水泥砂浆台阶", "石材台阶", "块料台阶",
            "平面砂浆找平层",
        ]
        for name in common_names:
            if name in text:
                boq_name = name
                break

    # ── 2. 检测外部依赖 ──
    seen_positions: set[int] = set()
    for pattern, ref_type in SEE_DRAWING_PATTERNS:
        for match in pattern.finditer(text):
            pos = match.start()
            # 去重：相同位置只保留首次匹配
            if pos in seen_positions:
                continue
            # 去重：子串关系（如"详见图纸"已覆盖"见图纸"）
            is_subsumed = False
            for existing in external_refs:
                if (existing["position"] <= pos and
                    pos + len(match.group(0)) <= existing["position"] + len(existing["matched_text"])):
                    is_subsumed = True
                    break
            if is_subsumed:
                continue
            seen_positions.add(pos)
            affected = _guess_affected_fields(match.group(0), text)
            external_refs.append({
                "reference_type": ref_type,
                "matched_text": match.group(0).strip(),
                "position": pos,
                "affected_fields": affected,
            })

    # ── 3. 检测缺失字段 ──
    has_quantity = False
    m_qty = re.search(r"(?:工程量|数量)[：:]\s*([\d.]+)", text)
    if m_qty:
        try:
            qty = float(m_qty.group(1))
            if qty > 0:
                has_quantity = True
        except ValueError:
            pass

    m_unit = re.search(r"(?:计量单位|单位)[：:]\s*\S+", text)
    if not m_unit:
        missing_fields.append("计量单位")
    if not boq_code:
        missing_fields.append("项目编码")
    if not boq_name:
        missing_fields.append("项目名称")
    if not has_quantity:
        missing_fields.append("工程量")

    # 项目特征检测
    has_feature_text = False
    feature_section = re.search(r"项目特征[：:](.+?)(?=\n(?:工程量|计量单位|备注|$))", text, re.DOTALL)
    if feature_section:
        feature_content = feature_section.group(1).strip()
        if len(feature_content) > 5:  # 至少有一些实质内容
            has_feature_text = True
    # 也检查常见的特征字段
    feature_fields = re.findall(r"(?:厚度|强度|配合比|规格|品种|材质|等级|遍数)[：:]\s*\S+", text)
    if feature_fields:
        has_feature_text = True

    if not has_feature_text:
        missing_fields.append("项目特征")

    # ── 4. 编码-名称一致性 ──
    code_name_match = True
    code_name_detail = ""
    if boq_code and boq_name:
        standard_code = boq_code[:9] if len(boq_code) >= 9 else boq_code[:6]
        expected_keywords = CODE_NAME_CONSISTENCY.get(standard_code, [])
        if expected_keywords:
            name_lower = boq_name.lower()
            matched = any(kw.lower() in name_lower for kw in expected_keywords)
            if not matched:
                code_name_match = False
                code_name_detail = f"编码 {standard_code} 期望名称包含 {expected_keywords}，实际为「{boq_name}」"
                issues.append({
                    "severity": "warn",
                    "type": "code_name_mismatch",
                    "message": code_name_detail,
                    "field": "项目名称",
                })

    # ── 5. 构建问题和评分 ──
    if not boq_code:
        issues.append({
            "severity": "block",
            "type": "missing_code",
            "message": "缺少项目编码，无法进行标准匹配",
            "field": "项目编码",
        })

    if boq_code and len(boq_code) < 9:
        issues.append({
            "severity": "warn",
            "type": "short_code",
            "message": f"编码 {boq_code} 不足9位，标准匹配精度下降",
            "field": "项目编码",
        })

    for ref in external_refs:
        severity = "block" if ref["reference_type"] in ("详见图纸", "参照图纸") else "warn"
        issues.append({
            "severity": severity,
            "type": "external_reference",
            "message": f"清单包含外部依赖：{ref['matched_text']}（{ref['reference_type']}）",
            "field": "项目特征",
            "affected_fields": ref["affected_fields"],
        })

    for field in missing_fields:
        if field != "项目特征":  # 项目特征的缺失在其他检测中处理
            issues.append({
                "severity": "warn" if field in ("计量单位",) else "block",
                "type": "missing_field",
                "message": f"缺少字段：{field}",
                "field": field,
            })

    # ── 6. 计算质量评分 ──
    score = 1.0
    if not boq_code:
        score -= 0.3
    if not boq_name:
        score -= 0.15
    if not has_quantity:
        score -= 0.2
    if not has_feature_text:
        score -= 0.2
    if external_refs:
        score -= 0.1 * min(len(external_refs), 3)
        if score >= 0.9:
            score = 0.89  # 外部依赖上限不超过 good
    if not code_name_match:
        score -= 0.1
    score = max(0.0, min(1.0, score))

    if score >= 0.9:
        overall = "excellent"
    elif score >= 0.7:
        overall = "good"
    elif score >= 0.5:
        overall = "fair"
    else:
        overall = "poor"

    # 摘要
    parts = []
    if boq_code:
        parts.append(f"编码: {boq_code}")
    if boq_name:
        parts.append(f"名称: {boq_name}")
    parts.append(f"质量: {overall} ({score:.0%})")
    if external_refs:
        parts.append(f"外部依赖: {len(external_refs)}处")
    if missing_fields:
        parts.append(f"缺失字段: {', '.join(missing_fields)}")
    if not code_name_match:
        parts.append("编码-名称不一致")

    return BoqPreCheckResult(
        overall_quality=overall,
        quality_score=round(score, 4),
        boq_code=boq_code,
        boq_name=boq_name,
        has_quantity=has_quantity,
        has_feature_text=has_feature_text,
        has_external_ref=len(external_refs) > 0,
        external_refs=external_refs,
        missing_fields=missing_fields,
        code_name_match=code_name_match,
        code_name_detail=code_name_detail,
        issues=issues,
        summary="；".join(parts) if parts else "无法解析",
    )


def _guess_affected_fields(matched_text: str, full_text: str) -> list[str]:
    """推测外部依赖影响的字段范围"""
    affected = []
    text_lower = full_text.lower()
    matched_lower = matched_text.lower()

    # 见图纸通常影响规格、厚度等具体参数
    if any(kw in matched_lower for kw in ["图纸", "图集"]):
        affected.extend(["面层规格", "面层厚度", "结合层厚度", "缝宽"])
    if any(kw in matched_lower for kw in ["设计", "设计要求"]):
        affected.extend(["材料品种", "规格", "厚度", "配合比"])
    if any(kw in matched_lower for kw in ["暂定", "待报", "待定"]):
        affected.extend(["材料品种", "规格", "工程量"])

    return list(dict.fromkeys(affected))  # 去重保持顺序


# ══════════════════════════════════════
# 批量清单质量扫描
# ══════════════════════════════════════

@dataclass
class BatchScanResult:
    """批量扫描结果"""
    total_lines: int
    results: list[BoqPreCheckResult]
    quality_distribution: dict[str, int]  # {"excellent": N, "good": N, ...}
    external_ref_lines: int
    missing_code_lines: int
    missing_feature_lines: int
    auto_processable_ratio: float  # 可自动处理比例
    needs_review_ratio: float  # 需人工复核比例
    summary: str


def scan_boq_batch(lines: list[str]) -> BatchScanResult:
    """批量扫描多条清单行"""
    results = [scan_boq_content(line) for line in lines if line.strip()]
    if not results:
        return BatchScanResult(
            total_lines=0, results=[], quality_distribution={},
            external_ref_lines=0, missing_code_lines=0, missing_feature_lines=0,
            auto_processable_ratio=0.0, needs_review_ratio=0.0, summary="无有效清单行"
        )

    total = len(results)
    dist = {"excellent": 0, "good": 0, "fair": 0, "poor": 0}
    external_count = 0
    missing_code_count = 0
    missing_feature_count = 0

    for r in results:
        dist[r.overall_quality] = dist.get(r.overall_quality, 0) + 1
        if r.has_external_ref:
            external_count += 1
        if "项目编码" in r.missing_fields:
            missing_code_count += 1
        if "项目特征" in r.missing_fields:
            missing_feature_count += 1

    auto_ratio = (dist["excellent"] + dist["good"]) / total
    review_ratio = (dist["poor"] + external_count * 0.5) / total

    return BatchScanResult(
        total_lines=total,
        results=results,
        quality_distribution=dist,
        external_ref_lines=external_count,
        missing_code_lines=missing_code_count,
        missing_feature_lines=missing_feature_count,
        auto_processable_ratio=round(auto_ratio, 4),
        needs_review_ratio=round(min(1.0, review_ratio), 4),
        summary=f"共{total}行: 可自动处理{auto_ratio:.0%}, 需复核{review_ratio:.0%}, "
                f"外部依赖{external_count}行, 缺失编码{missing_code_count}行, 缺失特征{missing_feature_count}行",
    )
