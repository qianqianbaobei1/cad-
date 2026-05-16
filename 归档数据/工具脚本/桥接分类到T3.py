#!/usr/bin/env python3
"""R4: 分类材料库 ↔ T3标准物料库 双向映射桥接。

链路：
  分类库 material_name → T3 标准名称/别名 → t3_id
  T3 material_id → 分类库 categories → category_ids

输出：
  - 更新 三级分类材料库.json 的 materials，每个匹配项新增 t3_id 字段
  - 更新 T3_品类映射.csv，新增 matched_category_ids、matched_category_count 列
"""

from __future__ import annotations

import csv
import json
import re
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CLASSIFICATION_PATH = ROOT / "标准知识库" / "三级分类材料库.json"
T3_MAP_PATH = ROOT / "标准知识库" / "源数据" / "06_品类树" / "T3_品类映射.csv"
T3_FJ_CSV = ROOT / "标准知识库" / "T3_房建_标准物料库.csv"
T3_AZ_CSV = ROOT / "标准知识库" / "T3_安装_标准物料库.csv"


def normalize(s: str) -> str:
    s = re.sub(r"\s+", "", str(s))
    s = s.lower()
    s = s.replace("（", "(").replace("）", ")")
    return s


def load_t3_index() -> dict[str, list[str]]:
    """构建 T3 名称→material_id 索引（标准名+别名）。返回 {normalized_name: [material_ids]}"""
    idx: dict[str, list[str]] = defaultdict(list)
    for csv_path in [T3_FJ_CSV, T3_AZ_CSV]:
        if not csv_path.exists():
            continue
        with open(csv_path, "r", encoding="utf-8-sig") as f:
            for row in csv.DictReader(f):
                mid = row.get("物料ID", "").strip()
                if not mid:
                    continue
                name = row.get("标准名称", "").strip()
                if name:
                    idx[normalize(name)].append(mid)
                aliases_raw = row.get("别名", "").strip()
                if aliases_raw and aliases_raw not in ("[]", "null", ""):
                    try:
                        aliases = json.loads(aliases_raw)
                        for a in aliases:
                            a_clean = a.strip()
                            if a_clean:
                                idx[normalize(a_clean)].append(mid)
                    except (json.JSONDecodeError, TypeError):
                        pass
    return dict(idx)


def match_classification_to_t3(
    cat_kb: dict, t3_name_index: dict[str, list[str]]
) -> tuple[int, int, dict[str, set[str]]]:
    """将分类库中每个材料匹配 T3 material_id。
    返回 (matched_count, total_count, t3_to_categories: {t3_id: {category_ids}})
    """
    matched = 0
    total = 0
    t3_to_cats: dict[str, set[str]] = defaultdict(set)

    for cat in cat_kb.get("categories", []):
        cat_id = cat.get("category_id", "")
        for mat in cat.get("materials", []):
            total += 1
            mat_name = mat.get("material_name", "").strip()
            if not mat_name:
                continue

            # Exact match on normalized name
            nm = normalize(mat_name)
            if nm in t3_name_index:
                t3_ids = t3_name_index[nm]
                mat["t3_id"] = t3_ids[0]  # first match
                if len(t3_ids) > 1:
                    mat["t3_id_candidates"] = t3_ids
                matched += 1
                for tid in t3_ids:
                    t3_to_cats[tid].add(cat_id)
                continue

            # Try matching aliases
            for alias in mat.get("aliases", []):
                alias_nm = normalize(str(alias))
                if alias_nm in t3_name_index:
                    t3_ids = t3_name_index[alias_nm]
                    mat["t3_id"] = t3_ids[0]
                    matched += 1
                    for tid in t3_ids:
                        t3_to_cats[tid].add(cat_id)
                    break

    return matched, total, dict(t3_to_cats)


def update_t3_mapping(t3_to_cats: dict[str, set[str]]) -> tuple[int, int]:
    """更新 T3_品类映射.csv，添加 matched_category_ids 列。"""
    if not T3_MAP_PATH.exists():
        return 0, 0

    rows = []
    with open(T3_MAP_PATH, "r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames or []
        for row in reader:
            rows.append(row)

    # Add new columns if not present
    new_cols = []
    for col in ["matched_category_ids", "matched_category_count"]:
        if col not in (fieldnames or []):
            new_cols.append(col)

    updated = 0
    for row in rows:
        mid = row.get("material_id", row.get("﻿material_id", "")).strip()
        cat_ids = t3_to_cats.get(mid, set())
        row["matched_category_ids"] = "|".join(sorted(cat_ids))
        row["matched_category_count"] = str(len(cat_ids))
        if cat_ids:
            updated += 1

    all_fields = list(fieldnames) + new_cols
    with open(T3_MAP_PATH, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=all_fields)
        writer.writeheader()
        writer.writerows(rows)

    return updated, len(rows)


def main():
    print("=" * 60)
    print("R4: 分类 ↔ T3 双向映射桥接")
    print("=" * 60)

    # 1. Load T3 name index
    print("\n[1/4] 加载 T3 名称索引...")
    t3_idx = load_t3_index()
    print(f"  T3 唯一名称/别名: {len(t3_idx)}")

    # 2. Load classification library
    print("\n[2/4] 加载分类材料库...")
    with open(CLASSIFICATION_PATH, "r", encoding="utf-8") as f:
        cat_kb = json.load(f)
    total_mats = sum(len(cat.get("materials", [])) for cat in cat_kb.get("categories", []))
    print(f"  分类条目: {len(cat_kb.get('categories',[]))}, 材料总数: {total_mats}")

    # 3. Match
    print("\n[3/4] 匹配分类材料 → T3...")
    matched, total, t3_to_cats = match_classification_to_t3(cat_kb, t3_idx)
    print(f"  匹配成功: {matched}/{total} ({matched/total*100:.1f}%)")
    print(f"  覆盖 T3 物料: {len(t3_to_cats)}/329")

    # 4. Save classification
    print("\n[4/4] 保存更新...")
    cat_kb["summary"]["t3_bridged_count"] = matched
    cat_kb["summary"]["t3_bridged_at"] = "2026-05-15"
    backup = CLASSIFICATION_PATH.with_suffix(".json.bak_r4")
    with open(backup, "w", encoding="utf-8") as f:
        json.dump(cat_kb, f, ensure_ascii=False, indent=2)  # save backup first
    with open(CLASSIFICATION_PATH, "w", encoding="utf-8") as f:
        json.dump(cat_kb, f, ensure_ascii=False, indent=2)
    print(f"  分类库已更新 (备份: {backup.name})")

    # 5. Update T3 mapping
    t3_updated, t3_total = update_t3_mapping(t3_to_cats)
    print(f"  T3_品类映射: {t3_updated}/{t3_total} 有分类关联")

    # Show sample matches
    print("\n=== 匹配样本 ===")
    count = 0
    for cat in cat_kb.get("categories", []):
        for mat in cat.get("materials", []):
            if mat.get("t3_id") and count < 15:
                print(f"  [{mat['material_name']}] → {mat['t3_id']} (分类:{cat['category_path']})")
                count += 1

    print(f"\n✅ R4 完成：分类↔T3双向映射已建立")


if __name__ == "__main__":
    main()
