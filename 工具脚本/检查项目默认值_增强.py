#!/usr/bin/env python3
"""增强版项目默认值库质量检查。区分AI候选和人工策展的检查标准。

检查分级:
  ERROR   — 会导致流水线出错或算量失败
  WARN    — 降低准确性但不阻断流程
  INFO    — 统计信息
"""
from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
KB_PATH = ROOT / "项目数据" / "项目默认值库.json"

SUPPORTED_FORMULA_MARKERS = [
    "BOQ_AREA", "BOQ_QTY", "BOQ_VOLUME", "BOQ_LENGTH", "BOQ_COUNT",
    "BOQ_WEIGHT", "THICKNESS_MM", "USAGE_PER_M2", "USAGE_PER_M",
    "COEFF", "LOSS",
]

RUNTIME_PARAMS = {
    "loss_rate", "thickness_mm", "thickness", "density", "coeff",
    "coverage_rate", "coverage", "coats", "usage_per_m", "usage_per_m2",
    "usage_per_ton", "turnover_times", "turnover", "ratio",
    "blocks_per_m3", "joint_depth", "joint_width",
    "cross_section", "density_per_m2", "unit_weight",
    "dft", "solids", "rebound_rate", "rebound_coeff",
}


def main() -> int:
    kb = json.loads(KB_PATH.read_text(encoding="utf-8"))
    systems = {s.get("system_id") for s in kb.get("default_systems", []) or []}
    recs = kb.get("material_recommendations", []) or []
    refs = {r.get("ref_id") or r.get("standard_ref_id") or r.get("code")
            for r in kb.get("standard_refs", []) or []}

    errors: list[str] = []
    warnings: list[str] = []
    formula_counter = Counter()
    source_counter = Counter()

    seen_rec_ids = set()
    for idx, rec in enumerate(recs, start=1):
        rec_id = rec.get("rec_id") or f"row-{idx}"
        source_type = rec.get("source_type", "unknown")
        source_counter[source_type] += 1

        # ── 通用检查 ──
        if rec_id in seen_rec_ids:
            errors.append(f"DUPLICATE {rec_id}: rec_id重复")
        seen_rec_ids.add(rec_id)

        if not rec.get("material_name") or rec["material_name"] == "None":
            errors.append(f"EMPTY_NAME {rec_id}: material_name为空/None")

        sid = rec.get("system_id", "")
        if sid and sid != "None" and sid not in systems:
            errors.append(f"ORPHAN {rec_id}: system_id={sid} 在default_systems中不存在")

        # ── 执行字段检查（区分AI候选 vs 人工策展） ──
        is_ai = source_type == "project_default_kb_ai_candidate"

        # loss_rate_hint — AI候选可以暂缺（能算量时手动填）
        lr = rec.get("loss_rate_hint")
        if lr is None:
            if is_ai:
                warnings.append(f"MISSING_LOSS {rec_id}: loss_rate_hint缺失 (AI候选)")
            else:
                errors.append(f"MISSING_LOSS {rec_id}: loss_rate_hint缺失 (人工策展)")
        elif isinstance(lr, (int, float)):
            if lr > 1.0:
                errors.append(f"BAD_LOSS {rec_id}: loss_rate_hint={lr} >1.0(可能是百分比,应转为小数)")
            elif lr > 0.5:
                warnings.append(f"HIGH_LOSS {rec_id}: loss_rate_hint={lr} 偏高(>0.5)")

        # quantity_rule
        rule = rec.get("quantity_rule") or {}
        if not isinstance(rule, dict):
            errors.append(f"BAD_RULE {rec_id}: quantity_rule不是dict")
        elif not rule.get("formula"):
            if is_ai:
                warnings.append(f"NO_FORMULA {rec_id}: 缺算量公式 (AI候选,仅作文本提示)")
            else:
                errors.append(f"NO_FORMULA {rec_id}: 缺算量公式 (人工策展应可执行)")
        else:
            formula = str(rule.get("formula"))
            formula_counter[formula] += 1

            # 检查公式是否用了合法变量
            tokens = set(re.findall(r'[A-Z_][A-Z_0-9]*', formula))
            known = {"BOQ_AREA", "BOQ_VOLUME", "BOQ_LENGTH", "BOQ_COUNT", "BOQ_WEIGHT",
                     "THICKNESS_MM", "USAGE_PER_M2", "USAGE_PER_M", "COEFF", "LOSS"}
            unknown = tokens - known - {"BOQ_QTY", "STEEL_QTY", "CRACK_LENGTH"}
            if unknown:
                warnings.append(f"UNKNOWN_VARS {rec_id}: 公式含未知变量 {unknown}")

            if "THICKNESS(m)" in formula:
                errors.append(f"THICKNESS_UNIT {rec_id}: 应用THICKNESS_MM替代THICKNESS(m)")

        # default_spec_values — AI候选可缺
        spec_vals = rec.get("default_spec_values")
        if not spec_vals or (isinstance(spec_vals, dict) and len(spec_vals) == 0):
            if not is_ai:  # 只有人工策展时才报warn
                warnings.append(f"NO_SPEC {rec_id}: default_spec_values为空")

        # basis
        basis = rec.get("basis")
        if not basis or (isinstance(basis, list) and len(basis) == 0) or \
           (isinstance(basis, str) and not basis.strip()):
            warnings.append(f"NO_BASIS {rec_id}: 无规范依据")

        # standard_ref_ids
        srefs = rec.get("standard_ref_ids")
        if not srefs or (isinstance(srefs, list) and len(srefs) == 0):
            if not is_ai:
                warnings.append(f"NO_STD_REF {rec_id}: 无标准引用")
        elif isinstance(srefs, list):
            for ref in srefs:
                if refs and ref not in refs:
                    warnings.append(f"UNKNOWN_REF {rec_id}: standard_ref_id={ref} 不在库中")

        # required_params 不能为None
        if rec.get("required_params") is None:
            errors.append(f"NULL_PARAMS {rec_id}: required_params不能为None, 应为[]")

        # AI候选安全边界检查
        if is_ai:
            if rec.get("can_auto_add") is True:
                errors.append(f"AI_AUTO_ADD {rec_id}: AI候选can_auto_add必须为false")
            if rec.get("t3_match_required") is not True:
                warnings.append(f"AI_NO_T3 {rec_id}: AI候选未标记t3_match_required=true")
            conf = rec.get("confidence", "")
            if conf not in ("medium", "low"):
                warnings.append(f"AI_CONF {rec_id}: AI候选confidence应为medium/low, 实际={conf}")

    # 系统级别检查
    sys_mat_count = Counter()
    for rec in recs:
        sid = rec.get("system_id", "unknown")
        if sid and sid != "None":
            sys_mat_count[sid] += 1
    orphan_systems = systems - set(sys_mat_count.keys())
    if orphan_systems:
        warnings.append(f"ORPHAN_SYSTEMS: {len(orphan_systems)}个系统无任何材料关联")

    # ── 输出报告 ──
    has_errors = len(errors) > 0
    has_warnings = len(warnings) > 0

    print("PASS" if not has_errors else "FAIL", "项目默认值库 (增强检查)")
    print(f"systems: {len(systems)} (含材料的: {len(sys_mat_count)}, 空系统: {len(orphan_systems)})")
    print(f"materials: {len(recs)}")
    print(f"  人工策展(project_default_kb): {source_counter.get('project_default_kb', 0)}")
    print(f"  AI候选(ai_candidate): {source_counter.get('project_default_kb_ai_candidate', 0)}")
    print(f"  其他: {sum(v for k,v in source_counter.items() if k not in ('project_default_kb', 'project_default_kb_ai_candidate'))}")
    print(f"formula_families: {len(formula_counter)}")
    print(f"ERRORS: {len(errors)}")
    print(f"WARNINGS: {len(warnings)}")

    if errors:
        # 按错误类型分组统计
        err_types = Counter()
        for e in errors:
            err_types[e.split(" ", 1)[0]] += 1
        print("\n错误类型分布:")
        for t, c in err_types.most_common():
            print(f"  {t}: {c}")
        print("\n错误详情 (前30):")
        for err in errors[:30]:
            print(f"  {err}")
        if len(errors) > 30:
            print(f"  ... {len(errors) - 30} more")

    if warnings:
        warn_types = Counter()
        for w in warnings:
            warn_types[w.split(" ", 1)[0]] += 1
        print("\n警告类型分布:")
        for t, c in warn_types.most_common():
            print(f"  {t}: {c}")

    # ── 综合健康度评分 ──
    total = len(recs)
    exec_count = sum(1 for r in recs
                     if isinstance(r.get("quantity_rule"), dict)
                     and r["quantity_rule"].get("formula"))
    loss_count = sum(1 for r in recs if r.get("loss_rate_hint") is not None)
    spec_count = sum(1 for r in recs if r.get("default_spec_values")
                     and isinstance(r["default_spec_values"], dict)
                     and len(r["default_spec_values"]) > 0)
    basis_count = sum(1 for r in recs if r.get("basis"))
    stdref_count = sum(1 for r in recs if r.get("standard_ref_ids"))

    print(f"\n── 健康度评分 ──")
    print(f"  可执行率(formula): {exec_count}/{total} ({100*exec_count/total:.1f}%)")
    print(f"  损耗率覆盖: {loss_count}/{total} ({100*loss_count/total:.1f}%)")
    print(f"  规格默认值覆盖: {spec_count}/{total} ({100*spec_count/total:.1f}%)")
    print(f"  规范依据覆盖: {basis_count}/{total} ({100*basis_count/total:.1f}%)")
    print(f"  标准引用覆盖: {stdref_count}/{total} ({100*stdref_count/total:.1f}%)")

    # 综合评分（仅算人工策展的可执行率）
    manual = [r for r in recs if r.get("source_type") == "project_default_kb"]
    if manual:
        m_exec = sum(1 for r in manual
                     if isinstance(r.get("quantity_rule"), dict)
                     and r["quantity_rule"].get("formula"))
        m_loss = sum(1 for r in manual if r.get("loss_rate_hint") is not None)
        print(f"  人工策展可执行率: {m_exec}/{len(manual)} ({100*m_exec/len(manual):.1f}%)")
        print(f"  人工策展损耗率覆盖: {m_loss}/{len(manual)} ({100*m_loss/len(manual):.1f}%)")

    return 1 if has_errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
