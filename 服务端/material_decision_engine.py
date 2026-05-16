"""
材料裁决引擎 — 确定性规则引擎，替代 AI 做材料存在性判断。

核心职责：
  1. 加载 material_decision_rules.json
  2. 按 boq_code 匹配规则
  3. 逐条评估 condition（特征关键词/项目上下文）
  4. 决定每个材料：REQUIRED→必须包含 / CONDITIONAL→条件评估 / OPTIONAL→默认不输出 / FORBIDDEN→禁止 / EXCLUDED→排除
  5. 分配辅材 S1/S2/S3 分级

设计原则：LLM 只提取不裁决。这个引擎做所有的材料存在性决定。
"""
import json
import re
from pathlib import Path
from dataclasses import dataclass, field

ROOT = Path(__file__).resolve().parents[1]
RULES_PATH = ROOT / "标准知识库" / "源数据" / "附录L" / "material_decision_rules.json"


# ══════════════════════════════════════
# 数据模型
# ══════════════════════════════════════

@dataclass
class MaterialDecision:
    """单个材料的裁决结果"""
    material_name: str
    material_id: str
    role: str  # 主材/辅材
    decision: str  # REQUIRED/CONDITIONAL/OPTIONAL/FORBIDDEN/EXCLUDED
    is_included: bool
    aux_grade: str | None  # S1/S2/S3 or None
    priority: int
    quantity_formula: str
    quantity_unit: str
    waste_rate: float
    process_type: str
    condition_met: bool | None  # None=无条件, True=条件满足, False=条件不满足
    condition_detail: str
    source: str  # "material_decision_rule"
    evidence: str
    notes: str


@dataclass
class DecisionResult:
    """一条清单项的完整裁决结果"""
    boq_code: str
    boq_name: str
    total_rules_matched: int

    # 各类裁决的材料
    required_materials: list[MaterialDecision] = field(default_factory=list)
    conditional_included: list[MaterialDecision] = field(default_factory=list)
    conditional_excluded: list[MaterialDecision] = field(default_factory=list)
    optional_materials: list[MaterialDecision] = field(default_factory=list)
    forbidden_materials: list[MaterialDecision] = field(default_factory=list)
    excluded_materials: list[MaterialDecision] = field(default_factory=list)

    # 最终采购清单材料（已排序）
    included_materials: list[MaterialDecision] = field(default_factory=list)

    # 辅材分级汇总
    aux_s1_materials: list[MaterialDecision] = field(default_factory=list)
    aux_s2_materials: list[MaterialDecision] = field(default_factory=list)
    aux_s3_materials: list[MaterialDecision] = field(default_factory=list)

    # 质量标记
    missing_required: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def summarize(self) -> dict:
        return {
            "total_materials": len(self.included_materials),
            "required_count": len(self.required_materials),
            "conditional_included": len(self.conditional_included),
            "optional_available": len(self.optional_materials),
            "forbidden_blocked": len(self.forbidden_materials),
            "s1_aux_count": len(self.aux_s1_materials),
            "s2_aux_count": len(self.aux_s2_materials),
            "s3_aux_count": len(self.aux_s3_materials),
            "missing_required": self.missing_required,
            "warnings": self.warnings,
        }


# ══════════════════════════════════════
# 规则加载与缓存
# ══════════════════════════════════════

_rules_cache = None


def _load_rules(force_reload: bool = False) -> dict:
    """加载材料裁决规则，返回 {boq_code: [rules]}"""
    global _rules_cache
    if _rules_cache is not None and not force_reload:
        return _rules_cache
    _rules_cache = {}
    if not RULES_PATH.exists():
        return _rules_cache
    with open(RULES_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)
    for rule in data.get("rules", []):
        code = rule.get("boq_code", "").strip()
        if not code:
            continue
        _rules_cache.setdefault(code, []).append(rule)
    # 按优先级降序排列
    for code in _rules_cache:
        _rules_cache[code].sort(key=lambda r: r.get("priority", 0), reverse=True)
    return _rules_cache


# ══════════════════════════════════════
# 条件评估
# ══════════════════════════════════════

def _evaluate_condition(
    condition: dict | None,
    feature_text: str,
    features: dict,
    project_context: dict,
) -> tuple[bool, str]:
    """评估规则的条件是否满足。返回 (是否满足, 原因描述)。

    condition 结构:
      - 简单形式: {"keyword_in_feature": ["A", "B"]}
      - 复合形式: {"operator": "AND/OR", "conditions": [...]}

    支持的 condition source:
      - feature_text: 在项目特征原文中搜索
      - extracted_field: 在已提取字段中检查
      - project_context: 在项目上下文中检查
    """
    if condition is None:
        return True, "无条件触发"

    # 简单 keyword_in_feature 形式
    if "keyword_in_feature" in condition:
        keywords = condition["keyword_in_feature"]
        if not keywords:
            return True, "無關鍵詞條件"
        text_lower = (feature_text + " " + " ".join(str(v) for v in features.values())).lower()
        matched = [kw for kw in keywords if kw.lower() in text_lower]
        if matched:
            return True, f"特征命中: {', '.join(matched)}"
        else:
            return False, f"特征未命中关键词: {', '.join(keywords)}"

    # 复合条件
    operator = condition.get("operator", "AND")
    sub_conditions = condition.get("conditions", [])
    if not sub_conditions:
        return True, "無子條件"

    results = []
    for sub in sub_conditions:
        source = sub.get("source", "")
        field = sub.get("field", "")
        op = sub.get("op", "contains")
        value = sub.get("value", "")

        if source == "feature_text" or not source:
            text = feature_text.lower()
        elif source == "extracted_field":
            if field and field in features:
                text = str(features[field]).lower()
            else:
                results.append((False, f"字段 {field} 不存在"))
                continue
        elif source == "project_context":
            if field and field in project_context:
                text = str(project_context[field]).lower()
            else:
                results.append((False, f"项目上下文 {field} 不存在"))
                continue
        else:
            text = feature_text.lower()

        if op == "contains":
            ok = value.lower() in text
            detail = f"{'✓' if ok else '✗'} {source}.{field} contains '{value}'"
            results.append((ok, detail))
        elif op == "exists":
            ok = bool(text.strip())
            detail = f"{'✓' if ok else '✗'} {source}.{field} exists"
            results.append((ok, detail))
        elif op == "=":
            ok = text.strip() == value.lower()
            detail = f"{'✓' if ok else '✗'} {source}.{field} == '{value}'"
            results.append((ok, detail))
        else:
            results.append((False, f"未知操作符: {op}"))

    if operator == "AND":
        all_ok = all(r[0] for r in results)
        detail = " AND ".join(r[1] for r in results)
        return all_ok, detail
    elif operator == "OR":
        any_ok = any(r[0] for r in results)
        detail = " OR ".join(r[1] for r in results)
        return any_ok, detail
    else:
        return False, f"未知运算符: {operator}"


# ══════════════════════════════════════
# 主裁决函数
# ══════════════════════════════════════

def apply_material_decisions(
    boq_code: str,
    boq_name: str,
    feature_text: str,
    features: dict,
    project_context: dict | None = None,
) -> DecisionResult:
    """对一条清单项执行材料裁决。

    Args:
        boq_code: 12位清单编码
        boq_name: 清单项名称
        feature_text: 项目特征原文
        features: 已解析的项目特征字段 {字段名: 值}
        project_context: 项目上下文 {building_part, structure_type, ...}

    Returns:
        DecisionResult 包含所有裁决结果
    """
    if project_context is None:
        project_context = {}

    rules = _load_rules()
    standard_code = boq_code[:9] if len(boq_code) >= 9 else boq_code[:6]
    parent_code = boq_code[:6] if len(boq_code) >= 6 else ""

    # 匹配规则：精确匹配 + 父级匹配
    matched_rules = list(rules.get(standard_code, []))
    if parent_code and parent_code != standard_code:
        matched_rules.extend(rules.get(parent_code, []))

    # 也匹配 section_code 级别的通用规则
    section_code = boq_code[:4] if len(boq_code) >= 4 else ""
    if section_code and section_code != parent_code:
        # 查找所有以 section_code 开头的规则键
        for code_key, code_rules in rules.items():
            if code_key.startswith(section_code) and code_key not in (standard_code, parent_code):
                for r in code_rules:
                    # 只取 EXCLUDED 规则（跨子类排除）
                    if r.get("decision") == "EXCLUDED":
                        matched_rules.append(r)

    # 去重
    seen_ids = set()
    unique_rules = []
    for r in matched_rules:
        rid = r.get("rule_id", "")
        if rid not in seen_ids:
            seen_ids.add(rid)
            unique_rules.append(r)
    matched_rules = sorted(unique_rules, key=lambda r: r.get("priority", 0), reverse=True)

    result = DecisionResult(
        boq_code=boq_code,
        boq_name=boq_name,
        total_rules_matched=len(matched_rules),
    )

    # 逐条评估
    for rule in matched_rules:
        decision_type = rule.get("decision", "")
        condition = rule.get("condition")
        forbidden_if = rule.get("forbidden_if")

        # 评估主条件
        cond_met, cond_detail = _evaluate_condition(condition, feature_text, features, project_context)

        # 评估禁止条件（FORBIDDEN 的第二重条件）
        forbidden_met = False
        forbidden_detail = ""
        if forbidden_if:
            forbidden_met, forbidden_detail = _evaluate_condition(
                forbidden_if, feature_text, features, project_context
            )

        md = MaterialDecision(
            material_name=rule.get("material_name", ""),
            material_id=rule.get("material_id", ""),
            role=rule.get("role", "主材"),
            decision=decision_type,
            is_included=False,
            aux_grade=rule.get("aux_grade"),
            priority=rule.get("priority", 50),
            quantity_formula=rule.get("quantity_formula", ""),
            quantity_unit=rule.get("quantity_unit", ""),
            waste_rate=rule.get("waste_rate", 0),
            process_type=rule.get("process_type", ""),
            condition_met=None,
            condition_detail="",
            source="material_decision_rule",
            evidence=rule.get("evidence", ""),
            notes=rule.get("notes", ""),
        )

        if decision_type == "REQUIRED":
            md.is_included = True
            md.condition_met = True
            md.condition_detail = "强制包含"
            result.required_materials.append(md)
            result.included_materials.append(md)
            if md.aux_grade == "S1":
                result.aux_s1_materials.append(md)

        elif decision_type == "CONDITIONAL":
            md.condition_met = cond_met
            md.condition_detail = cond_detail
            if cond_met:
                md.is_included = True
                result.conditional_included.append(md)
                result.included_materials.append(md)
                if md.aux_grade == "S1":
                    result.aux_s1_materials.append(md)
                elif md.aux_grade == "S2":
                    result.aux_s2_materials.append(md)
                elif md.aux_grade == "S3":
                    result.aux_s3_materials.append(md)
            else:
                result.conditional_excluded.append(md)

        elif decision_type == "OPTIONAL":
            md.condition_met = None
            md.condition_detail = "默认不输出，用户可选择添加"
            result.optional_materials.append(md)
            # S3 辅材也记录但不纳入采购清单
            if md.aux_grade == "S3":
                result.aux_s3_materials.append(md)

        elif decision_type == "FORBIDDEN":
            md.condition_met = forbidden_met
            md.condition_detail = forbidden_detail
            if forbidden_met:
                md.is_included = False
                result.forbidden_materials.append(md)
                result.warnings.append(f"禁止材料 {md.material_name}: {forbidden_detail}")

        elif decision_type == "EXCLUDED":
            md.condition_met = None
            md.condition_detail = "该编码永远不应出现此材料"
            result.excluded_materials.append(md)

    # 按优先级排序最终清单
    result.included_materials.sort(key=lambda m: m.priority, reverse=True)

    # 检查是否有必需材料缺失（对 REQUIRED 材料名称与已包含材料对比）
    # 这里只标记没有匹配到规则的情况
    if not result.required_materials and not result.conditional_included:
        result.warnings.append("没有命中任何REQUIRED或CONDITIONAL规则，可能需要AI补充")

    return result


# ══════════════════════════════════════
# 辅助函数：裁决结果转流水线 item 格式
# ══════════════════════════════════════

def decision_to_pipeline_format(result: DecisionResult) -> list[dict]:
    """将 DecisionResult 转为流水线中 ai_materials 的格式，向后兼容。"""
    items = []
    for md in result.included_materials:
        items.append({
            "material_name": md.material_name,
            "material_id": md.material_id,
            "spec_hint": "",
            "role": md.role,
            "unit": md.quantity_unit,
            "supply": "乙供",
            "waste_rate_estimate": md.waste_rate,
            "confidence": "high",
            "reason": f"裁决规则: {md.decision} | {md.evidence}",
            "source": "material_decision_rule",
            "trigger_process": md.process_type,
            "aux_grade": md.aux_grade,
            "decision_type": md.decision,
            "condition_met": md.condition_met,
            "condition_detail": md.condition_detail,
        })
    return items


def get_aux_grade_summary(result: DecisionResult) -> dict:
    """获取辅材分级汇总"""
    return {
        "S1_必要辅材": [
            {"name": m.material_name, "condition": m.condition_detail}
            for m in result.aux_s1_materials
        ],
        "S2_条件辅材": [
            {"name": m.material_name, "condition": m.condition_detail}
            for m in result.aux_s2_materials
        ],
        "S3_零星辅材": [
            {"name": m.material_name, "condition": m.condition_detail}
            for m in result.aux_s3_materials
        ],
        "建议": {
            "S1": "默认输出，缺失需人工确认",
            "S2": "条件满足时输出",
            "S3": "默认不输出，可选择性添加",
        },
    }


def match_forbidden_materials(
    ai_materials: list[dict],
    boq_code: str,
    feature_text: str = "",
) -> list[dict]:
    """检查 AI 返回的材料中是否有命中 EXCLUDED/FORBIDDEN 规则的。

    Returns: 需要剔除的材料列表
    """
    rules = _load_rules()
    standard_code = boq_code[:9] if len(boq_code) >= 9 else boq_code[:6]
    matched_rules = rules.get(standard_code, [])

    to_exclude = []
    for mat in ai_materials:
        mat_name = mat.get("material_name", "")
        for rule in matched_rules:
            if rule.get("decision") in ("EXCLUDED", "FORBIDDEN"):
                rule_mat = rule.get("material_name", "")
                # 精确或模糊匹配
                if mat_name == rule_mat or rule_mat in mat_name or mat_name in rule_mat:
                    to_exclude.append({
                        "material_name": mat_name,
                        "rule_id": rule.get("rule_id"),
                        "decision": rule.get("decision"),
                        "reason": rule.get("evidence", ""),
                    })
                    break
    return to_exclude
