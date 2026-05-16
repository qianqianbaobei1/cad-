#!/usr/bin/env python3
"""Normalize executable fields in data/项目默认值库.json.

This script only fixes deterministic execution metadata. It does not invent new
systems or materials.
"""
from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
KB_PATH = ROOT / "项目数据" / "项目默认值库.json"


def normalize_rule(rec: dict) -> list[str]:
    changed: list[str] = []
    name = str(rec.get("material_name") or "")
    rule = rec.get("quantity_rule") or {}
    if not isinstance(rule, dict):
        rule = {}
        rec["quantity_rule"] = rule

    formula = str(rule.get("formula") or "")
    if "BOQ_AREA * THICKNESS(m) / 1000" in formula:
        rule["formula"] = formula.replace("THICKNESS(m)", "THICKNESS_MM")
        rule["formula_desc"] = "面积(m²) × 厚度(mm)÷1000 × (1+损耗率)"
        rule["unit_conversion"] = "m²(BOQ面积) × 厚度(mm)÷1000 → m³"
        changed.append("fixed_thickness_unit")

    compact = name.replace(" ", "")
    if "专用粘接剂" in compact:
        rule.update(
            {
                "formula": "BOQ_AREA * USAGE_PER_M2 * (1 + LOSS)",
                "formula_desc": "面积(m²) × 单方粘接剂用量(kg/m²) × (1+损耗率)",
                "depends_on": ["usage_per_m2", "loss_rate"],
                "default_usage_per_m2": 5.0,
                "unit_conversion": "m²(保温面积) → kg(粘接剂)",
                "note": "薄抹灰外保温粘接剂常用量约4-6kg/m²，缺设计说明时取5kg/m²并强制复核。",
            }
        )
        rec["unit_hint"] = "kg"
        changed.append("fixed_insulation_adhesive_area_rule")

    if "瓷砖胶粘剂" in compact:
        rule.update(
            {
                "formula": "BOQ_AREA * USAGE_PER_M2 * (1 + LOSS)",
                "formula_desc": "面积(m²) × 单方胶粘剂用量(kg/m²) × (1+损耗率)",
                "depends_on": ["usage_per_m2", "loss_rate"],
                "default_usage_per_m2": 4.0,
                "unit_conversion": "m²(铺贴面积) → kg(瓷砖胶粘剂)",
                "note": "薄贴法常用量约3-6kg/m²，缺设计说明时取4kg/m²并强制复核。",
            }
        )
        rec["unit_hint"] = "kg"
        changed.append("fixed_tile_adhesive_area_rule")

    if any(k in compact for k in ["地砖", "玻化砖", "瓷砖", "墙砖", "面砖"]) and "胶粘剂" not in compact:
        rule.update(
            {
                "formula": "BOQ_AREA * COEFF * (1 + LOSS)",
                "formula_desc": "铺贴面积(m²) × 排版/切割系数 × (1+损耗率)",
                "depends_on": ["coeff", "loss_rate"],
                "default_coeff": 1.05,
                "unit_conversion": "m²(铺贴面积) → m²(砖材)",
                "note": "块材按排版、切割和破损损耗综合，缺专项排版时取1.05并强制复核。",
            }
        )
        rec["unit_hint"] = "m²"
        changed.append("fixed_tile_area_rule")

    if "蒸压加气混凝土砌块" in compact:
        rule["default_blocks_per_m3"] = 8.33
        changed.append("added_aac_blocks_per_m3")
    elif any(k in compact for k in ["烧结普通砖", "烧结多孔砖", "空心砖"]):
        rule["default_blocks_per_m3"] = 512 if "普通砖" in compact else 320
        changed.append("added_brick_blocks_per_m3")

    if any(k in compact for k in ["密封胶", "密封膏", "发泡剂", "美缝剂", "胶粘带"]):
        if "USAGE_PER_M" in str(rule.get("formula", "")):
            rule["depends_on"] = ["usage_per_m", "loss_rate"]
            rule.setdefault("default_usage_per_m", 0.25)
            changed.append("fixed_sealant_depends_on")

    if "橡胶止水带" in compact:
        rule.update(
            {
                "formula": "BOQ_LENGTH * COEFF * (1 + LOSS)",
                "formula_desc": "止水带长度(m) × 搭接/损耗前系数 × (1+损耗率)",
                "depends_on": ["coeff", "loss_rate"],
                "default_coeff": 1.05,
                "unit_conversion": "m(施工缝长度) → m(止水带)",
                "note": "含搭接和转角余量，缺详图时取1.05并强制复核。",
            }
        )
        rec["unit_hint"] = "m"
        changed.append("fixed_waterstop_length_rule")

    if any(k in compact for k in ["轻钢龙骨石膏板隔墙", "穿孔石膏板吸音吊顶", "纸面石膏板吊顶"]):
        rule.update(
            {
                "formula": "BOQ_AREA * COEFF * (1 + LOSS)",
                "formula_desc": "展开面积(m²) × 排版/损耗系数 × (1+损耗率)",
                "depends_on": ["coeff", "loss_rate"],
                "default_coeff": 1.05,
                "unit_conversion": "m²(清单面积) → m²(板材/体系)",
                "note": "板材体系按排版切割和损耗综合，缺详图时取1.05并强制复核。",
            }
        )
        rec["unit_hint"] = "m²"
        changed.append("fixed_board_system_area_rule")

    if any(k in compact for k in ["注浆液", "堵漏王", "锚杆"]):
        if "CRACK_LENGTH" in str(rule.get("formula", "")):
            rule.setdefault("default_cross_section", 0.0001)
            changed.append("added_crack_cross_section_default")

    if "抗裂砂浆" in compact or "抹面砂浆" in compact:
        # 保温薄抹灰砂浆的常规厚度比普通抹灰小，缺失时取 6mm。
        if rec.get("system_id") and "INS" in rec.get("system_id", ""):
            rule.setdefault("default_density", 0.0018)
            rule["default_thickness"] = 6
            rec.setdefault("default_spec_values", {})
            if isinstance(rec["default_spec_values"], dict):
                rec["default_spec_values"]["thickness"] = "6mm"
            changed.append("fixed_insulation_mortar_default_thickness")

    return changed


def main() -> int:
    kb = json.loads(KB_PATH.read_text(encoding="utf-8"))
    changes = []
    for rec in kb.get("material_recommendations", []) or []:
        for change in normalize_rule(rec):
            changes.append((rec.get("rec_id"), rec.get("material_name"), change))

    KB_PATH.write_text(json.dumps(kb, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"normalized: {len(changes)} changes")
    for rec_id, name, change in changes[:80]:
        print(f"- {rec_id} {name}: {change}")
    if len(changes) > 80:
        print(f"... {len(changes) - 80} more")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
