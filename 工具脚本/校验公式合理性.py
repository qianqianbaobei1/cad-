#!/usr/bin/env python3
"""校验项目默认值库中的算量公式是否与材料类型/单位匹配。

公式-材料匹配规则:
  - 体积类材料(m³) → BOQ_VOLUME 或 BOQ_AREA * THICKNESS_MM
  - 面积类材料(m²) → BOQ_AREA 或 BOQ_LENGTH * WIDTH
  - 长度类材料(m) → BOQ_LENGTH 或 BOQ_COUNT * LENGTH
  - 重量类材料(kg/t) → BOQ_WEIGHT 或 BOQ_VOLUME * DENSITY
  - 计数类材料(个/套) → BOQ_COUNT
  - 消耗类(粘接剂/涂料/kg) → BOQ_AREA * USAGE_PER_M2 或 BOQ_LENGTH * USAGE_PER_M
"""
from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
KB_PATH = ROOT / "项目数据" / "项目默认值库.json"

# 材料名→合理公式类型的启发式规则
CATEGORY_FORMULA_RULES = [
    # (名称关键词, 合理变量, 不合理变量, 说明)
    ("混凝土|砌块|砖|砂浆|回填|土方|毛石|碎石|中砂|砂砾",
     {"BOQ_VOLUME", "THICKNESS_MM"}, {"USAGE_PER_M2", "USAGE_PER_M"},
     "体积类材料"),
    ("保温板|挤塑|岩棉|XPS|EPS|聚苯板|聚氨酯板|玻璃棉|橡塑",
     {"BOQ_AREA", "THICKNESS_MM"}, {"BOQ_VOLUME", "USAGE_PER_M"},
     "保温材料(面积×厚度→m³)"),
    ("防水卷材|卷材|SBS|APP|PVC卷材|TPO|HDPE卷材|自粘",
     {"BOQ_AREA", "COEFF"}, {"THICKNESS_MM", "USAGE_PER_M"},
     "防水卷材(面积×系数→m²)"),
    ("涂料|漆|防水涂料|防火涂料|乳胶漆|真石漆|腻子|底漆|面漆",
     {"BOQ_AREA", "USAGE_PER_M2", "COEFF", "COATS"}, {"BOQ_VOLUME"},
     "涂覆材料(面积×单耗)"),
    ("粘接剂|胶粘剂|密封胶|美缝剂|填缝剂|发泡剂|堵漏|注浆|灌浆",
     {"BOQ_AREA", "USAGE_PER_M2", "BOQ_LENGTH", "USAGE_PER_M"}, {"BOQ_VOLUME", "BOQ_COUNT"},
     "消耗性材料(面积/长度×单耗)"),
    ("管材|管道|钢管|塑料管|镀锌管|无缝管|焊接管|铸铁管|PE管|PPR|PVC|UPVC|HDPE",
     {"BOQ_LENGTH", "COEFF"}, {"BOQ_AREA", "USAGE_PER_M2"},
     "管材(长度×系数)"),
    ("电缆|电线|导线|光缆|光纤|母线|桥架|线槽",
     {"BOQ_LENGTH", "COEFF"}, {"BOQ_AREA", "THICKNESS_MM"},
     "线材(长度×系数)"),
    ("钢筋|型钢|钢板|钢管|扁钢|圆钢|角钢|槽钢|工字钢|H型钢",
     {"BOQ_WEIGHT", "BOQ_LENGTH", "COEFF"}, {"USAGE_PER_M2"},
     "钢材(重量/长度×系数)"),
    ("门|窗|百叶|卷帘|防火门|防盗门|木门|铝合金门|钢门|塑钢窗|铝合金窗",
     {"BOQ_COUNT"}, {"BOQ_VOLUME", "USAGE_PER_M2"},
     "成品构件(计数)"),
    ("地砖|瓷砖|面砖|墙砖|石材|花岗岩|大理石|地板|地毯|壁纸|石膏板|硅酸钙板|矿棉板|扣板",
     {"BOQ_AREA", "COEFF"}, {"BOQ_VOLUME", "THICKNESS_MM", "USAGE_PER_M2"},
     "面层材料(面积×系数)"),
    ("模板|脚手架|支撑|龙骨|吊杆",
     {"BOQ_AREA", "COEFF"}, {"BOQ_VOLUME"},
     "周转材料(面积×系数)"),
    ("混凝土|预拌|商品混凝土|细石混凝土",
     {"BOQ_VOLUME", "COEFF"}, {"BOQ_AREA", "USAGE_PER_M2"},
     "混凝土(体积×系数)"),
    ("止水带|止水条|密封条|胶条",
     {"BOQ_LENGTH", "COEFF", "USAGE_PER_M"}, {"BOQ_AREA", "BOQ_VOLUME"},
     "线型材料(长度×系数)"),
    ("螺栓|锚栓|螺钉|螺母|垫圈|钉|化学锚栓|膨胀螺栓",
     {"BOQ_COUNT", "BOQ_WEIGHT"}, {"BOQ_AREA", "BOQ_VOLUME", "THICKNESS_MM"},
     "紧固件(计数/重量)"),
    ("瓦|小青瓦|沥青瓦|彩瓦|混凝土瓦|脊瓦",
     {"BOQ_AREA", "COEFF"}, {"BOQ_VOLUME"},
     "屋面瓦(面积×系数)"),
]


def extract_variables(formula: str) -> set[str]:
    """从公式中提取变量名"""
    tokens = set(re.findall(r'[A-Z_][A-Z_0-9]*', formula))
    return {t for t in tokens if t not in ("BOQ_QTY", "STEEL_QTY", "STEEL_TON",
                                             "STEEL_AREA", "WIDTH", "FLOOR_COUNT",
                                             "CRACK_LENGTH", "DUCT_LENGTH", "FRAME_LENGTH",
                                             "GLASS_PERIMETER")}


def check_formula_consistency(material: dict) -> list[str]:
    """检查公式与材料类型是否匹配，返回问题列表"""
    name = material.get("material_name", "")
    unit_hint = material.get("unit_hint", "")
    rule = material.get("quantity_rule") or {}
    formula = rule.get("formula", "")

    if not formula:
        return []

    issues = []
    vars_used = extract_variables(formula)
    unit_lower = (unit_hint or "").lower()

    # 检查每个规则
    for pattern, expected_vars, forbidden_vars, desc in CATEGORY_FORMULA_RULES:
        if re.search(pattern, name):
            # 检查是否使用了不合理的变量
            actual_forbidden = vars_used & forbidden_vars
            if actual_forbidden:
                issues.append(f"[{desc}] 公式含不合理变量 {actual_forbidden}，"
                            f"材料={name[:30]}，公式={formula[:60]}")

            # 检查是否缺少关键变量
            # (只在有预期变量时才检查)
            # expected_vars 不强制要求全部存在
            break

    # 单位-公式一致性
    if "m³" in unit_lower and vars_used and "BOQ_VOLUME" not in vars_used \
       and "THICKNESS_MM" not in vars_used:
        # m³材料必须由体积或面积×厚度得到
        issues.append(f"单位m³但公式不含BOQ_VOLUME或THICKNESS_MM: {name[:30]}, formula={formula[:60]}")

    if "m²" in unit_lower and vars_used and "BOQ_AREA" not in vars_used \
       and "BOQ_COUNT" not in vars_used:
        issues.append(f"单位m²但公式不含BOQ_AREA或BOQ_COUNT: {name[:30]}, formula={formula[:60]}")

    if unit_lower in ("个", "套", "台", "樘", "扇") and vars_used \
       and "BOQ_COUNT" not in vars_used:
        issues.append(f"计数单位但公式不含BOQ_COUNT: {name[:30]}, formula={formula[:60]}")

    return issues


def main() -> int:
    with open(KB_PATH, encoding="utf-8") as f:
        kb = json.load(f)

    materials = kb.get("material_recommendations", [])
    total = len(materials)
    with_formula = 0
    issues_found = []
    by_category = defaultdict(list)

    for m in materials:
        rule = m.get("quantity_rule") or {}
        formula = rule.get("formula", "")
        if formula:
            with_formula += 1
            result = check_formula_consistency(m)
            if result:
                for r in result:
                    issues_found.append({
                        "rec_id": m.get("rec_id"),
                        "material_name": m.get("material_name"),
                        "issue": r
                    })
                    # 分类
                    cat = m.get("system_id", "unknown")[:30]
                    by_category[cat].append(r)

    print("=" * 60)
    print("公式合理性校验")
    print("=" * 60)
    print(f"总材料: {total}, 有公式: {with_formula}")
    print(f"公式问题: {len(issues_found)}")

    if issues_found:
        # 按问题类型分组
        issue_types = defaultdict(int)
        for iss in issues_found:
            # 提取问题类型
            t = iss["issue"].split("]")[0].replace("[", "") if "]" in iss["issue"] else "其他"
            issue_types[t] += 1

        print(f"\n问题类型分布:")
        for t, c in sorted(issue_types.items(), key=lambda x: -x[1]):
            print(f"  {t}: {c}")

        print(f"\n问题详情 (前30):")
        for iss in issues_found[:30]:
            rec_id = iss["rec_id"]
            name = iss["material_name"]
            detail = iss["issue"]
            print(f"  {rec_id} {name}: {detail[:120]}")
        if len(issues_found) > 30:
            print(f"  ... {len(issues_found) - 30} more")

    print(f"\n结果: {'⚠️ 有问题需修复' if issues_found else '✅ 全部通过'}")
    return 1 if issues_found else 0


if __name__ == "__main__":
    raise SystemExit(main())
