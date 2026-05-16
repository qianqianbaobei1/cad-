#!/usr/bin/env python3
"""生成 CCE 三级分类材料库。

目标很简单：
1. 读取 CCE JSON 的三级/末级分类；
2. 读取每个末级分类下的标准材料品种；
3. 合并 CCE 原始别名和人工补充别称；
4. 生成可用于材料归类的 JSON 索引和 CSV 明细。
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
from collections import defaultdict
from datetime import datetime
from pathlib import Path


流程目录 = Path(__file__).resolve().parents[1]
数据目录 = 流程目录 / "数据"
导出目录 = 流程目录 / "导出"

默认源文件 = Path(os.getenv("CCE_SOURCE_FILE", "/Users/qianqianawodebaobei/PycharmProjects/CCE_Full_Data_1778492694.json"))
默认人工别称 = 数据目录 / "三级分类材料别称人工补充.csv"
默认AI别称 = 数据目录 / "三级分类材料别称AI回填.csv"
默认输出JSON = 数据目录 / "三级分类材料库.json"
默认分类CSV = 导出目录 / "三级分类材料汇总.csv"
默认材料CSV = 导出目录 / "三级分类材料明细.csv"
默认别名CSV = 导出目录 / "三级分类材料别名索引.csv"


def 读JSON(path: Path) -> dict:
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def 写JSON(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def 读CSV(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def 写CSV(path: Path, rows: list[dict], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def 归一(value: str) -> str:
    text = str(value or "").strip().lower()
    text = text.replace("（", "(").replace("）", ")")
    text = re.sub(r"[\s　,，;；、。/\\]+", "", text)
    return text


def 拆分类路径(path: str) -> tuple[str, str, str]:
    parts = [p.strip() for p in str(path or "").split(">") if p.strip()]
    return (
        parts[0] if len(parts) > 0 else "",
        parts[1] if len(parts) > 1 else "",
        parts[2] if len(parts) > 2 else "",
    )


def 展开末级分类(nodes: list[dict] | None, parents: list[str] | None = None) -> list[dict]:
    parents = parents or []
    rows: list[dict] = []
    for node in nodes or []:
        label = str(node.get("label", "")).strip()
        path = parents + ([label] if label else [])
        children = node.get("children") or []
        if children:
            rows.extend(展开末级分类(children, path))
            continue
        rows.append({
            "category_id": str(node.get("value", "")),
            "category_l1": path[0] if len(path) > 0 else "",
            "category_l2": path[1] if len(path) > 1 else "",
            "category_l3": path[2] if len(path) > 2 else "",
            "category_path": " > ".join(path),
        })
    return rows


def 解析别名(value) -> list[str]:
    if not value or value == "-":
        return []
    if isinstance(value, list):
        raw = value
    else:
        raw = re.split(r"[、,，;；]\s*", str(value))
    return [str(x).strip() for x in raw if str(x).strip()]


def 加别名(alias_map: dict[tuple[str, str], list[dict]], category_id: str, material_name: str, alias: str, source: str, note: str = "") -> None:
    alias = str(alias or "").strip()
    if not alias or 归一(alias) == 归一(material_name):
        return
    rows = alias_map[(category_id, material_name)]
    key = (归一(alias), source)
    if any((归一(r["alias"]), r["source"]) == key for r in rows):
        return
    rows.append({"alias": alias, "source": source, "note": note})


def 加分类别名(category_alias_map: dict[str, list[dict]], category_id: str, category_name: str, alias: str, source: str, note: str = "") -> None:
    alias = str(alias or "").strip()
    if not alias or 归一(alias) == 归一(category_name):
        return
    rows = category_alias_map[category_id]
    key = (归一(alias), source)
    if any((归一(r["alias"]), r["source"]) == key for r in rows):
        return
    rows.append({"alias": alias, "source": source, "note": note})


def 合并别称文件(path: Path, materials_by_key: dict[tuple[str, str], dict], categories_by_id: dict[str, dict], alias_map, category_alias_map) -> None:
    for row in 读CSV(path):
        category_id = str(row.get("末级分类ID", "")).strip()
        material_name = str(row.get("材料名称", "")).strip()
        alias = str(row.get("别称", "")).strip()
        category = categories_by_id.get(category_id, {})
        if (category_id, material_name) in materials_by_key:
            加别名(alias_map, category_id, material_name, alias, row.get("来源", "manual") or "manual", row.get("备注", ""))
            continue
        # 有些常用说法指向的是末级分类，而不是具体品种。
        # 例如“商砼/商品混凝土”对应“普通混凝土”这个末级分类，CCE 下没有名为“普通混凝土”的品种行。
        if category and (not material_name or 归一(material_name) == 归一(category.get("category_l3", ""))):
            加分类别名(
                category_alias_map,
                category_id,
                category.get("category_l3", ""),
                alias,
                row.get("来源", "manual") or "manual",
                row.get("备注", ""),
            )


def main() -> int:
    parser = argparse.ArgumentParser(description="生成 CCE 三级分类材料库")
    parser.add_argument("--源文件", dest="source", default=str(默认源文件))
    parser.add_argument("--人工别称", dest="manual_alias", default=str(默认人工别称))
    parser.add_argument("--AI别称", dest="ai_alias", default=str(默认AI别称))
    parser.add_argument("--输出JSON", dest="output_json", default=str(默认输出JSON))
    parser.add_argument("--分类CSV", dest="category_csv", default=str(默认分类CSV))
    parser.add_argument("--材料CSV", dest="material_csv", default=str(默认材料CSV))
    parser.add_argument("--别名CSV", dest="alias_csv", default=str(默认别名CSV))
    args = parser.parse_args()

    raw = 读JSON(Path(args.source))
    categories_by_id = {row["category_id"]: row for row in 展开末级分类(raw.get("category_tree", []) or [])}
    materials_by_key: dict[tuple[str, str], dict] = {}
    alias_map: dict[tuple[str, str], list[dict]] = defaultdict(list)
    category_alias_map: dict[str, list[dict]] = defaultdict(list)

    for row in raw.get("data", []) or []:
        category_id = str(row.get("分类ID", "")).strip()
        category_path = str(row.get("分类路径", "")).strip()
        l1, l2, l3 = 拆分类路径(category_path)
        categories_by_id[category_id] = {
            "category_id": category_id,
            "category_l1": l1,
            "category_l2": l2,
            "category_l3": l3,
            "category_path": category_path,
        }
        material_name = str(row.get("品种名称", "")).strip()
        if not material_name:
            continue
        key = (category_id, material_name)
        materials_by_key[key] = {
            "material_name": material_name,
            "material_code": row.get("品种编码", ""),
            "category_id": category_id,
            "category_l1": l1,
            "category_l2": l2,
            "category_l3": l3,
            "category_path": category_path,
        }
        for alias in 解析别名(row.get("别名")):
            加别名(alias_map, category_id, material_name, alias, "cce_alias")

    合并别称文件(Path(args.manual_alias), materials_by_key, categories_by_id, alias_map, category_alias_map)
    合并别称文件(Path(args.ai_alias), materials_by_key, categories_by_id, alias_map, category_alias_map)

    category_groups: dict[str, list[dict]] = defaultdict(list)
    name_index: dict[str, list[dict]] = defaultdict(list)
    alias_rows = []
    material_rows = []

    for key, material in sorted(materials_by_key.items(), key=lambda x: (x[1]["category_path"], x[1]["material_name"])):
        category_id, material_name = key
        aliases = alias_map.get(key, [])
        material_item = dict(material)
        material_item["aliases"] = aliases
        category_groups[category_id].append(material_item)

        index_values = [{"name": material_name, "source": "standard_name"}] + [
            {"name": a["alias"], "source": a["source"]}
            for a in aliases
        ]
        for item in index_values:
            idx_key = 归一(item["name"])
            if not idx_key:
                continue
            name_index[idx_key].append({
                "input_name": item["name"],
                "source": item["source"],
                "category_id": category_id,
                "category_path": material["category_path"],
                "category_l3": material["category_l3"],
                "material_name": material_name,
            })

        material_rows.append({
            "末级分类ID": category_id,
            "一级分类": material["category_l1"],
            "二级分类": material["category_l2"],
            "末级分类名称": material["category_l3"],
            "分类路径": material["category_path"],
            "材料名称": material_name,
            "材料编码": material["material_code"],
            "别称俗称": "、".join(a["alias"] for a in aliases),
            "别称数量": len(aliases),
        })
        for alias in aliases:
            alias_rows.append({
                "末级分类ID": category_id,
                "末级分类名称": material["category_l3"],
                "分类路径": material["category_path"],
                "材料名称": material_name,
                "别称": alias["alias"],
                "来源": alias["source"],
                "备注": alias.get("note", ""),
            })

    categories = []
    category_rows = []
    for category_id, category in sorted(categories_by_id.items(), key=lambda x: x[1]["category_path"]):
        materials = category_groups.get(category_id, [])
        category_aliases = category_alias_map.get(category_id, [])
        alias_count = sum(len(m.get("aliases", [])) for m in materials) + len(category_aliases)
        category_item = dict(category)
        category_item["material_count"] = len(materials)
        category_item["alias_count"] = alias_count
        category_item["category_aliases"] = category_aliases
        category_item["materials"] = materials
        categories.append(category_item)
        for alias in category_aliases:
            idx_key = 归一(alias["alias"])
            if idx_key:
                name_index[idx_key].append({
                    "input_name": alias["alias"],
                    "source": alias["source"],
                    "category_id": category_id,
                    "category_path": category.get("category_path", ""),
                    "category_l3": category.get("category_l3", ""),
                    "material_name": category.get("category_l3", ""),
                })
            alias_rows.append({
                "末级分类ID": category_id,
                "末级分类名称": category.get("category_l3", ""),
                "分类路径": category.get("category_path", ""),
                "材料名称": "",
                "别称": alias["alias"],
                "来源": alias["source"],
                "备注": alias.get("note", ""),
            })
        category_rows.append({
            "末级分类ID": category_id,
            "一级分类": category.get("category_l1", ""),
            "二级分类": category.get("category_l2", ""),
            "末级分类名称": category.get("category_l3", ""),
            "分类路径": category.get("category_path", ""),
            "材料数量": len(materials),
            "别称数量": alias_count,
            "材料名称列表": "、".join(m["material_name"] for m in materials),
        })

    payload = {
        "version": "1.0",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "source_file": str(args.source),
        "manual_alias_file": str(args.manual_alias),
        "ai_alias_file": str(args.ai_alias),
        "summary": {
            "category_count": len(categories),
            "category_with_material_count": sum(1 for c in categories if c["material_count"]),
            "material_count": len(materials_by_key),
            "alias_count": len(alias_rows),
            "name_index_count": len(name_index),
        },
        "categories": categories,
        "name_index": dict(name_index),
    }

    写JSON(Path(args.output_json), payload)
    写CSV(Path(args.category_csv), category_rows, ["末级分类ID", "一级分类", "二级分类", "末级分类名称", "分类路径", "材料数量", "别称数量", "材料名称列表"])
    写CSV(Path(args.material_csv), material_rows, ["末级分类ID", "一级分类", "二级分类", "末级分类名称", "分类路径", "材料名称", "材料编码", "别称俗称", "别称数量"])
    写CSV(Path(args.alias_csv), alias_rows, ["末级分类ID", "末级分类名称", "分类路径", "材料名称", "别称", "来源", "备注"])

    print(
        f"已生成三级分类材料库: 分类{payload['summary']['category_count']}个, "
        f"有材料分类{payload['summary']['category_with_material_count']}个, "
        f"材料{payload['summary']['material_count']}条, 别称{payload['summary']['alias_count']}条"
    )
    print(f"JSON: {args.output_json}")
    print(f"分类CSV: {args.category_csv}")
    print(f"材料CSV: {args.material_csv}")
    print(f"别名CSV: {args.alias_csv}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
