"""
人工纠错反馈闭环 — 记录用户修正，分析趋势，生成知识库更新建议。

四分类分流:
  1. project_exception — 只作用当前项目
  2. customer_preference — 写入客户配置
  3. general_rule_error — 进入通用规则审核流程
  4. new_material_or_method — 进入新工艺知识库待补充
"""
import json
import uuid
from pathlib import Path
from datetime import datetime, timedelta
from dataclasses import dataclass, field

ROOT = Path(__file__).resolve().parents[1]
FEEDBACK_LOG = ROOT / "过程数据" / "correction_feedback.jsonl"


# ══════════════════════════════════════
# 数据模型
# ══════════════════════════════════════

@dataclass
class CorrectionRecord:
    """单条纠错记录"""
    id: str
    timestamp: str
    run_id: str
    boq_code: str
    boq_name: str
    action: str  # modify / add / reject
    original_material: dict | None  # 系统推荐的材料
    corrected_material: dict | None  # 用户修改后的材料
    modified_fields: list[str] = field(default_factory=list)
    reason: str = ""
    reviewer: str = ""


@dataclass
class FeedbackAnalysis:
    """反馈分析结果"""
    total_corrections: int
    period_days: int
    by_action: dict[str, int]  # {"modify": N, "add": N, "reject": N}
    by_code: dict[str, int]  # {"011101001": N}
    by_material: dict[str, int]
    suggestions: list[dict]


# ══════════════════════════════════════
# 记录纠错
# ══════════════════════════════════════

def record_correction(
    run_id: str,
    boq_code: str,
    boq_name: str,
    action: str,
    original_item: dict | None = None,
    corrected_item: dict | None = None,
    reason: str = "",
    reviewer: str = "",
) -> CorrectionRecord:
    """记录一条用户纠错到 JSONL 文件"""
    FEEDBACK_LOG.parent.mkdir(parents=True, exist_ok=True)

    # 计算被修改的字段
    modified_fields = []
    if action == "modify" and original_item and corrected_item:
        for key in set(list(original_item.keys()) + list(corrected_item.keys())):
            if original_item.get(key) != corrected_item.get(key):
                modified_fields.append(key)

    record = CorrectionRecord(
        id=uuid.uuid4().hex[:10],
        timestamp=datetime.now().isoformat(),
        run_id=run_id,
        boq_code=boq_code,
        boq_name=boq_name,
        action=action,
        original_material=original_item,
        corrected_material=corrected_item,
        modified_fields=modified_fields,
        reason=reason,
        reviewer=reviewer,
    )

    with open(FEEDBACK_LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps({
            "id": record.id,
            "timestamp": record.timestamp,
            "run_id": record.run_id,
            "boq_code": record.boq_code,
            "boq_name": record.boq_name,
            "action": record.action,
            "original_material": record.original_material,
            "corrected_material": record.corrected_material,
            "modified_fields": record.modified_fields,
            "reason": record.reason,
            "reviewer": record.reviewer,
        }, ensure_ascii=False) + "\n")

    return record


# ══════════════════════════════════════
# 分析反馈
# ══════════════════════════════════════

def _load_recent_records(since_days: int = 30) -> list[dict]:
    """加载最近N天的反馈记录"""
    if not FEEDBACK_LOG.exists():
        return []
    cutoff = datetime.now() - timedelta(days=since_days)
    records = []
    with open(FEEDBACK_LOG, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
                ts = datetime.fromisoformat(r.get("timestamp", ""))
                if ts >= cutoff:
                    records.append(r)
            except (json.JSONDecodeError, ValueError):
                continue
    return records


def analyze_feedback_batch(since_days: int = 30, min_frequency: int = 3) -> FeedbackAnalysis:
    """分析近期反馈，生成知识库更新建议"""
    records = _load_recent_records(since_days)

    if not records:
        return FeedbackAnalysis(
            total_corrections=0, period_days=since_days,
            by_action={}, by_code={}, by_material={}, suggestions=[],
        )

    # 统计
    by_action = {}
    by_code = {}
    by_material = {}
    for r in records:
        action = r.get("action", "")
        by_action[action] = by_action.get(action, 0) + 1

        code = r.get("boq_code", "")[:9]
        if code:
            by_code[code] = by_code.get(code, 0) + 1

        mat_name = (r.get("corrected_material") or r.get("original_material") or {}).get("material_name", "")
        if mat_name:
            by_material[mat_name] = by_material.get(mat_name, 0) + 1

    # 生成建议
    suggestions = []

    # 高频修正编码 → 可能规则不完整
    for code, count in by_code.items():
        if count >= min_frequency:
            suggestions.append({
                "type": "general_rule_error",
                "target": f"boq_code={code}",
                "frequency": count,
                "suggestion": f"编码 {code} 有 {count} 次人工修正，建议审核裁决规则",
                "priority": "high" if count >= 5 else "medium",
            })

    # 高频修改材料 → 可能规格/存在性规则需要调整
    for mat_name, count in by_material.items():
        if count >= min_frequency:
            suggestions.append({
                "type": "general_rule_error",
                "target": f"material={mat_name}",
                "frequency": count,
                "suggestion": f"材料「{mat_name}」被人工修正 {count} 次，建议审核其裁决规则和默认规格",
                "priority": "high" if count >= 5 else "medium",
            })

    # 频繁reject → 可能是编码-材料不匹配
    reject_count = by_action.get("reject", 0)
    if reject_count >= min_frequency:
        reject_codes = {}
        for r in records:
            if r.get("action") == "reject":
                code = r.get("boq_code", "")[:9]
                reject_codes[code] = reject_codes.get(code, 0) + 1
        suggestions.append({
            "type": "general_rule_error",
            "target": "reject_analysis",
            "frequency": reject_count,
            "suggestion": f"有 {reject_count} 次材料被拒绝，高频拒绝编码: {dict(sorted(reject_codes.items(), key=lambda x: -x[1])[:5])}",
            "priority": "medium",
        })

    # 新材料 → 知识库补充
    add_records = [r for r in records if r.get("action") == "add"]
    if add_records:
        new_materials = set()
        for r in add_records:
            cm = r.get("corrected_material", {})
            mn = cm.get("material_name", "")
            if mn:
                new_materials.add(mn)
        if new_materials:
            suggestions.append({
                "type": "new_material_or_method",
                "target": "new_materials",
                "frequency": len(add_records),
                "suggestion": f"用户新增 {len(new_materials)} 种系统未推荐的材料: {', '.join(list(new_materials)[:10])}",
                "priority": "high",
            })

    return FeedbackAnalysis(
        total_corrections=len(records),
        period_days=since_days,
        by_action=by_action,
        by_code=by_code,
        by_material=by_material,
        suggestions=suggestions,
    )


# ══════════════════════════════════════
# 纠错分类
# ══════════════════════════════════════

def classify_correction(
    action: str,
    original: dict | None,
    corrected: dict | None,
    reason: str,
) -> str:
    """将纠错分类为四类之一。

    Returns:
        project_exception / customer_preference / general_rule_error / new_material_or_method
    """
    reason_lower = reason.lower()

    # 新材料或新工艺
    if action == "add" and not original:
        return "new_material_or_method"

    # 客户偏好关键词
    customer_keywords = ["公司", "甲方", "业主要求", "品牌", "习惯", "合同", "协议"]
    if any(kw in reason_lower for kw in customer_keywords):
        return "customer_preference"

    # 项目特殊关键词
    project_keywords = ["本项目", "这个项目", "特殊", "本工程", "现场", "实际"]
    if any(kw in reason_lower for kw in project_keywords):
        return "project_exception"

    # 规则错误（默认）
    if action in ("modify", "reject"):
        return "general_rule_error"

    return "project_exception"


# ══════════════════════════════════════
# 统计查询
# ══════════════════════════════════════

def get_feedback_stats() -> dict:
    """获取反馈系统的总体统计"""
    if not FEEDBACK_LOG.exists():
        return {"total_records": 0, "first_record": None, "last_record": None}

    records = []
    with open(FEEDBACK_LOG, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue

    if not records:
        return {"total_records": 0, "first_record": None, "last_record": None}

    return {
        "total_records": len(records),
        "first_record": records[0].get("timestamp"),
        "last_record": records[-1].get("timestamp"),
        "by_action": {
            action: sum(1 for r in records if r.get("action") == action)
            for action in {"modify", "add", "reject"}
        },
    }
