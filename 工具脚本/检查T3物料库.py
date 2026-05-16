#!/usr/bin/env python3
"""T3 标准物料库质量检查。检查物料完整性、规格模式JSON、规范覆盖。"""
from __future__ import annotations

import csv
import json
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE_DATA = ROOT / "标准知识库" / "源数据"

T3_FJ_PATH = SOURCE_DATA / "01_房屋建筑与装饰工程" / "CSV导出" / "03_t3_标准物料库.csv"
T3_AZ_PATH = SOURCE_DATA / "02_通用安装工程" / "CSV导出" / "02_t3_标准物料库.csv"
N5_PATH = SOURCE_DATA / "03_国家规范库" / "N5_材料规范映射.csv"
N3_PATH = SOURCE_DATA / "03_国家规范库" / "N3_材料技术参数定义.csv"

REQUIRED_FIELDS = ["物料ID", "标准名称", "采购单位", "分类(一级)", "分类(二级)", "分类(三级)"]
IMPORTANT_FIELDS = ["规格模式JSON", "标准代号", "品类标准损耗率", "适用范围(附录)", "适用范围(项目)"]


def load_csv(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with open(path, encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def validate_spec_json(val: str) -> tuple[bool, str]:
    """校验规格模式JSON"""
    if not val or val.strip() in ("[]", "{}", "null", "None", ""):
        return False, "缺失/空"

    try:
        parsed = json.loads(val)
    except json.JSONDecodeError as e:
        return False, f"JSON解析失败: {e}"

    if isinstance(parsed, list):
        if len(parsed) == 0:
            return False, "空数组"
        # 检查每个参数结构
        for i, p in enumerate(parsed):
            if not isinstance(p, dict):
                return False, f"参数[{i}]不是对象"
            if "param" not in p:
                return False, f"参数[{i}]缺param字段"
        required_params = sum(1 for p in parsed if p.get("required"))
        return True, f"OK({len(parsed)}参数, {required_params}必填)"
    elif isinstance(parsed, dict):
        return len(parsed) > 0, "对象格式(应改为数组)" if parsed else "空对象"
    else:
        return False, f"未知类型: {type(parsed).__name__}"


def main() -> int:
    t3_fj = load_csv(T3_FJ_PATH)
    t3_az = load_csv(T3_AZ_PATH)
    all_t3 = t3_fj + t3_az
    n5 = load_csv(N5_PATH)
    n3 = load_csv(N3_PATH)

    n5_material_ids = {r.get("material_id", "").strip() for r in n5}
    n3_material_ids = {r.get("material_id", "").strip() for r in n3}

    print("=" * 60)
    print("T3 标准物料库质量检查")
    print("=" * 60)
    print(f"房建: {len(t3_fj)}, 安装: {len(t3_az)}, 总计: {len(all_t3)}")
    print(f"N5规范映射: {len(n5)}条, 覆盖{len(n5_material_ids)}个物料")
    print(f"N3技术参数: {len(n3)}条, 覆盖{len(n3_material_ids)}个物料")

    errors = []
    warnings = []
    stats = Counter()

    material_ids = set()

    for i, r in enumerate(all_t3):
        mid = r.get("物料ID", "").strip()
        name = r.get("标准名称", "").strip()
        label = f"{mid}:{name}" if mid else f"row-{i}"

        # 重复ID检查
        if mid:
            if mid in material_ids:
                errors.append(f"DUP_ID {label}: 物料ID重复")
            material_ids.add(mid)

        # 必填字段
        for fld in REQUIRED_FIELDS:
            if not r.get(fld, "").strip():
                errors.append(f"MISSING_{fld.upper()} {label}: 缺{fld}")

        stats["total"] += 1

        # 规格模式JSON
        spec_val = r.get("规格模式JSON", "")
        spec_ok, spec_msg = validate_spec_json(spec_val)
        if spec_ok:
            stats["has_spec_json"] += 1
        else:
            stats["missing_spec_json"] += 1
            warnings.append(f"NO_SPEC {label}: 规格模式JSON — {spec_msg}")

        # 标准代号
        std_code = r.get("标准代号", "").strip()
        if std_code:
            stats["has_std_code"] += 1
            # 格式校验
            if not any(p in std_code for p in ["GB", "JGJ", "CECS", "CJJ", "JG", "TB"]):
                warnings.append(f"BAD_STD {label}: 标准代号格式可疑 — {std_code}")
        else:
            stats["missing_std_code"] += 1
            warnings.append(f"NO_STD {label}: 无标准代号")

        # 品类标准损耗率
        loss = r.get("品类标准损耗率", "").strip()
        if loss:
            stats["has_loss_rate"] += 1
        else:
            stats["missing_loss_rate"] += 1

        # N5规范映射覆盖
        if mid in n5_material_ids:
            stats["has_n5"] += 1
        else:
            stats["missing_n5"] += 1

        # N3参数覆盖
        if mid in n3_material_ids:
            stats["has_n3"] += 1
        else:
            stats["missing_n3"] += 1

        # 适用范围
        if r.get("适用范围(附录)", "").strip():
            stats["has_appendix_scope"] += 1
        else:
            stats["missing_appendix_scope"] += 1

    # ── 按分类统计缺项 ──
    cat_stats = Counter()
    cat_missing_spec = Counter()
    cat_missing_std = Counter()
    for r in all_t3:
        cat = r.get("分类(一级)", "其他")
        cat_stats[cat] += 1
        spec_val = r.get("规格模式JSON", "")
        spec_ok, _ = validate_spec_json(spec_val)
        if not spec_ok:
            cat_missing_spec[cat] += 1
        if not r.get("标准代号", "").strip():
            cat_missing_std[cat] += 1

    # ── 输出 ──
    has_errors = len(errors) > 0
    has_warnings = len(warnings) > 0

    print("PASS" if not has_errors else "FAIL", "T3标准物料库")
    print(f"ERRORS: {len(errors)}, WARNINGS: {len(warnings)}")

    print(f"\n── 字段覆盖 ──")
    total = stats["total"]
    print(f"  物料总数: {total}")
    print(f"  规格模式JSON: {stats['has_spec_json']}/{total} ({100*stats['has_spec_json']/total:.1f}%)")
    print(f"  标准代号: {stats['has_std_code']}/{total} ({100*stats['has_std_code']/total:.1f}%)")
    print(f"  品类标准损耗率: {stats['has_loss_rate']}/{total} ({100*stats['has_loss_rate']/total:.1f}%)")
    print(f"  N5规范映射: {stats['has_n5']}/{total} ({100*stats['has_n5']/total:.1f}%)")
    print(f"  N3技术参数: {stats['has_n3']}/{total} ({100*stats['has_n3']/total:.1f}%)")
    print(f"  适用范围(附录): {stats['has_appendix_scope']}/{total} ({100*stats['has_appendix_scope']/total:.1f}%)")

    print(f"\n── 按分类缺项 (TOP15) ──")
    sorted_cats = sorted(cat_stats.items(), key=lambda x: -(cat_missing_spec.get(x[0], 0) + cat_missing_std.get(x[0], 0)))
    for cat, cnt in sorted_cats[:15]:
        ms = cat_missing_spec.get(cat, 0)
        mstd = cat_missing_std.get(cat, 0)
        print(f"  {cat}: 共{cnt}, 缺规格{ms}, 缺标准{mstd}")

    if errors:
        print(f"\n── 错误 ({len(errors)}) ──")
        for e in errors[:20]:
            print(f"  {e}")

    if warnings:
        warn_types = Counter(w.split(" ", 1)[0] for w in warnings)
        print(f"\n── 警告类型 ({len(warnings)}条) ──")
        for t, c in warn_types.most_common():
            print(f"  {t}: {c}")

    # 整体评分
    coverage = (stats['has_spec_json'] + stats['has_std_code'] + stats['has_loss_rate']) / (total * 3)
    print(f"\n── 健康度 ──")
    print(f"  核心字段覆盖率: {coverage:.0%}")
    print(f"  规范链路完整度(N5+N3): {(stats['has_n5']+stats['has_n3'])/(total*2):.0%}")

    return 1 if has_errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
