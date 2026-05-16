#!/usr/bin/env python3
"""把 T3 标准物料库的丰富别名注入 CCE 三级分类材料库的 name_index。"""
from __future__ import annotations

import csv
import json
import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CCE_JSON = ROOT / "标准知识库" / "三级分类材料库.json"
T3_FJ_CSV = ROOT / "标准知识库" / "源数据" / "01_房屋建筑与装饰工程" / "CSV导出" / "03_t3_标准物料库.csv"
T3_AZ_CSV = ROOT / "标准知识库" / "源数据" / "02_通用安装工程" / "CSV导出" / "02_t3_标准物料库.csv"


def 归一(text: str) -> str:
    text = str(text or "").strip().lower()
    text = text.replace("（", "(").replace("）", ")")
    return re.sub(r"[\s　,，;；、。/\\]+", "", text)


def 解析JSON数组(s: str) -> list[str]:
    if not s or s in ("[]", "{}", ""):
        return []
    try:
        val = json.loads(s)
        if isinstance(val, list):
            return [str(x).strip() for x in val if str(x).strip()]
        elif isinstance(val, str):
            return [x.strip() for x in re.split(r"[、,，;；]\s*", val) if x.strip()]
    except (json.JSONDecodeError, TypeError):
        return [x.strip() for x in re.split(r"[、,，;；]\s*", str(s)) if x.strip()]


def 读取T3(csv_path: Path) -> list[dict]:
    rows = []
    if not csv_path.exists():
        return rows
    with csv_path.open(encoding="utf-8-sig", newline="") as f:
        for r in csv.DictReader(f):
            rows.append({
                "物料ID": r.get("物料ID", "").strip(),
                "标准名称": r.get("标准名称", "").strip(),
                "分类路径": r.get("分类路径", "").strip(),
                "分类_一级": r.get("分类(一级)", "").strip(),
                "分类_二级": r.get("分类(二级)", "").strip(),
                "分类_三级": r.get("分类(三级)", "").strip(),
                "别名": 解析JSON数组(r.get("别名", "")),
                "特征关键词": 解析JSON数组(r.get("特征关键词", "")),
                "品类树节点ID": r.get("品类树节点ID", "").strip(),
                "品类展示路径": r.get("品类展示路径", "").strip(),
            })
    return rows


def main() -> int:
    print("加载 CCE 三级分类材料库...")
    with CCE_JSON.open(encoding="utf-8") as f:
        cce = json.load(f)

    name_index = cce.get("name_index", {})
    categories = {c["category_id"]: c for c in cce.get("categories", [])}

    # 建立 CCE material_name → category 的反向索引
    material_to_category = {}
    for entries in name_index.values():
        for e in entries:
            mn = 归一(e.get("material_name", ""))
            if mn:
                material_to_category[mn] = {
                    "category_id": e.get("category_id", ""),
                    "category_path": e.get("category_path", ""),
                    "category_l3": e.get("category_l3", ""),
                }
    print(f"  CCE material_name 索引: {len(material_to_category)} 条")

    # 加载 T3
    t3_all = 读取T3(T3_FJ_CSV) + 读取T3(T3_AZ_CSV)
    print(f"  T3 物料: {len(t3_all)} 条")

    # 统计
    new_alias = 0
    new_material = 0
    source = "t3_alias"

    for t3 in t3_all:
        t3_name = t3["标准名称"]
        t3_name_key = 归一(t3_name)
        t3_category_path = t3["分类路径"]
        t3_category_l3 = t3["分类_三级"]

        # 尝试找到 CCE 对应的 category
        # 策略1: T3标准名称 精确匹配 CCE material_name
        cce_cat = material_to_category.get(t3_name_key)

        # 策略2: 没匹配到时，用 T3 自己的分类信息作为兜底
        if not cce_cat:
            cce_cat = {
                "category_id": t3["物料ID"],
                "category_path": t3_category_path,
                "category_l3": t3_category_l3,
            }

        # 注入 T3 标准名称本身
        if t3_name_key and t3_name_key not in name_index:
            name_index[t3_name_key] = []
        if t3_name_key in name_index:
            existing = {(归一(x["input_name"]), x["source"]) for x in name_index[t3_name_key]}
            if (归一(t3_name), source) not in existing:
                name_index[t3_name_key].append({
                    "input_name": t3_name,
                    "source": source,
                    "category_id": cce_cat["category_id"],
                    "category_path": cce_cat["category_path"],
                    "category_l3": cce_cat["category_l3"],
                    "material_name": t3_name,
                })
                new_material += 1

        # 注入 T3 别名
        for alias in t3["别名"]:
            alias_key = 归一(alias)
            if not alias_key or alias_key == t3_name_key:
                continue
            if alias_key not in name_index:
                name_index[alias_key] = []
            existing = {(归一(x["input_name"]), x["source"]) for x in name_index[alias_key]}
            if (归一(alias), source) not in existing:
                name_index[alias_key].append({
                    "input_name": alias,
                    "source": source,
                    "category_id": cce_cat["category_id"],
                    "category_path": cce_cat["category_path"],
                    "category_l3": cce_cat["category_l3"],
                    "material_name": t3_name,
                })
                new_alias += 1

        # 也注入特征关键词（低优先级，不是别称但可以作为搜索词）
        for kw in t3.get("特征关键词", [])[:5]:
            kw_key = 归一(kw)
            if not kw_key or kw_key == t3_name_key:
                continue
            if kw_key not in name_index:
                name_index[kw_key] = []
            existing = {(归一(x["input_name"]), x["source"]) for x in name_index[kw_key]}
            kw_source = "t3_keyword"
            if (归一(kw), kw_source) not in existing:
                name_index[kw_key].append({
                    "input_name": kw,
                    "source": kw_source,
                    "category_id": cce_cat["category_id"],
                    "category_path": cce_cat["category_path"],
                    "category_l3": cce_cat["category_l3"],
                    "material_name": t3_name,
                })

    # 更新摘要
    old_count = cce["summary"].get("alias_count", 0)
    old_index_count = cce["summary"].get("name_index_count", 0)
    cce["summary"]["alias_count"] = old_count + new_alias
    cce["summary"]["name_index_count"] = len(name_index)
    cce["summary"]["t3_bridged_at"] = __import__("datetime").datetime.now().isoformat(timespec="seconds")

    # 保存
    with CCE_JSON.open("w", encoding="utf-8") as f:
        json.dump(cce, f, ensure_ascii=False, indent=2)

    print(f"\n桥接完成:")
    print(f"  T3 标准名称注入: {new_material} 条")
    print(f"  T3 别名注入:     {new_alias} 条")
    print(f"  name_index 增长: {old_index_count} → {len(name_index)} 条")
    print(f"已保存: {CCE_JSON}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
