#!/usr/bin/env python3
"""确定性修复常见数据质量问题，无AI依赖。用于AI补全后的二次验证和清理。

修复项:
  1. loss_rate_hint > 1.0 → 除以100（百分比→小数）
  2. material_name 为空/None → 标记为"待确认材料"
  3. required_params 为 None → 改为 []
  4. basis 为字符串 → 标准化为数组
  5. quantity_rule.formula 中的 THICKNESS(m) → THICKNESS_MM
  6. 0.0 loss_rate_hint → None（0=无损耗，与缺失不同，但保留）
  7. 标准化 source_type 为统一值
"""
from __future__ import annotations

import json
import re
import shutil
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
KB_PATH = ROOT / "项目数据" / "项目默认值库.json"
BACKUP_DIR = ROOT / "过程数据" / "分类库备份"


def fix_loss_rate_hint(materials: list[dict]) -> int:
    """修复百分比格式的损耗率"""
    fixed = 0
    for m in materials:
        lr = m.get("loss_rate_hint")
        if isinstance(lr, (int, float)) and lr is not None:
            if lr > 1.0:
                m["loss_rate_hint"] = lr / 100.0
                fixed += 1
            elif lr < 0.001 and lr != 0:
                # 极小值可能是百分比转小数过度修正，保留但警告
                pass
    return fixed


def fix_empty_names(materials: list[dict]) -> int:
    """修复空材料名"""
    fixed = 0
    for m in materials:
        name = m.get("material_name")
        if name is None or (isinstance(name, str) and name.strip().lower() in ("", "none")):
            m["material_name"] = "待确认材料"
            fixed += 1
    return fixed


def fix_null_required_params(materials: list[dict]) -> int:
    """required_params 不能是 null"""
    fixed = 0
    for m in materials:
        if m.get("required_params") is None:
            m["required_params"] = []
            fixed += 1
    return fixed


def fix_basis_format(materials: list[dict]) -> int:
    """标准化 basis 格式"""
    fixed = 0
    for m in materials:
        basis = m.get("basis")
        if isinstance(basis, str) and basis.strip():
            m["basis"] = [basis.strip()]
            fixed += 1
        elif basis is None:
            m["basis"] = []
            fixed += 1
    return fixed


def fix_formula_thickness(materials: list[dict]) -> int:
    """修复 THICKNESS(m) → THICKNESS_MM"""
    fixed = 0
    for m in materials:
        rule = m.get("quantity_rule")
        if isinstance(rule, dict) and rule.get("formula"):
            formula = str(rule["formula"])
            if "THICKNESS(m)" in formula:
                rule["formula"] = formula.replace("THICKNESS(m)", "THICKNESS_MM")
                fixed += 1
    return fixed


def fix_source_types(materials: list[dict]) -> int:
    """标准化 source_type"""
    fixed = 0
    for m in materials:
        src = m.get("source_type", "")
        if src not in ("project_default_kb", "project_default_kb_ai_candidate"):
            m["source_type"] = "project_default_kb_ai_candidate"
            fixed += 1
    return fixed


def check_system_refs(materials: list[dict], systems: list[dict]) -> tuple[int, list[str]]:
    """检查材料引用的 system_id 是否存在"""
    system_ids = {s.get("system_id") for s in systems}
    orphans = []
    for m in materials:
        sid = m.get("system_id", "")
        if sid and sid != "None" and sid not in system_ids:
            orphans.append(f"{m.get('rec_id')}: system_id={sid}")
    return len(orphans), orphans


def main():
    print("=" * 60)
    print("修复常见数据问题")
    print("=" * 60)

    # 备份
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    ts = int(time.time())
    backup_path = BACKUP_DIR / f"项目默认值库_pre_fix_{ts}.json"
    shutil.copy2(KB_PATH, backup_path)
    print(f"已备份: {backup_path}")

    with open(KB_PATH, encoding="utf-8") as f:
        kb = json.load(f)

    materials = kb.get("material_recommendations", [])
    systems = kb.get("default_systems", [])
    total = len(materials)

    # 执行修复
    n_loss = fix_loss_rate_hint(materials)
    n_names = fix_empty_names(materials)
    n_params = fix_null_required_params(materials)
    n_basis = fix_basis_format(materials)
    n_formula = fix_formula_thickness(materials)
    n_src = fix_source_types(materials)
    n_orphans, orphan_list = check_system_refs(materials, systems)

    # 保存
    with open(KB_PATH, "w", encoding="utf-8") as f:
        json.dump(kb, f, ensure_ascii=False, indent=2)
        f.write("\n")

    print(f"\n修复统计 (共{total}条材料):")
    print(f"  损耗率百分比→小数: {n_loss}")
    print(f"  空材料名: {n_names}")
    print(f"  required_params null→[]: {n_params}")
    print(f"  basis 格式标准化: {n_basis}")
    print(f"  THICKNESS(m)→THICKNESS_MM: {n_formula}")
    print(f"  source_type标准化: {n_src}")
    print(f"  system_id不存在: {n_orphans}")

    if n_orphans > 0:
        print(f"\n孤儿材料 (system_id不存在):")
        for o in orphan_list[:20]:
            print(f"  {o}")
        if len(orphan_list) > 20:
            print(f"  ... {len(orphan_list) - 20} more")

    print(f"\n已保存: {KB_PATH}")


if __name__ == "__main__":
    main()
