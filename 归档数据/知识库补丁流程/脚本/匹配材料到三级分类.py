#!/usr/bin/env python3
"""把拆解出的材料名匹配到 CCE 三级分类材料库。"""
from __future__ import annotations

import argparse
import csv
import json
import re
from pathlib import Path


流程目录 = Path(__file__).resolve().parents[1]
数据目录 = 流程目录 / "数据"
导出目录 = 流程目录 / "导出"
默认材料库 = 数据目录 / "三级分类材料库.json"
默认输出 = 导出目录 / "材料三级分类匹配结果.csv"


def 读JSON(path: Path) -> dict:
    with path.open(encoding="utf-8") as f:
        return json.load(f)


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


def 读取材料(args) -> list[str]:
    names = []
    if args.materials:
        names.extend(args.materials)
    if args.input:
        with Path(args.input).open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    names.append(line)
    return list(dict.fromkeys(n.strip() for n in names if n.strip()))


def 匹配单个(name: str, kb: dict) -> dict:
    index = kb.get("name_index", {}) or {}
    key = 归一(name)
    exact = index.get(key, [])
    if exact:
        row = exact[0]
        return {
            "输入材料名": name,
            "匹配状态": "exact",
            "标准材料名": row.get("material_name", ""),
            "末级分类ID": row.get("category_id", ""),
            "末级分类名称": row.get("category_l3", ""),
            "分类路径": row.get("category_path", ""),
            "匹配依据": row.get("source", ""),
            "候选数量": len(exact),
        }

    candidates = []
    for idx_key, rows in index.items():
        if not idx_key:
            continue
        if idx_key in key or key in idx_key:
            score = min(len(idx_key), len(key))
            for row in rows[:3]:
                candidates.append((score, row))
    if candidates:
        candidates.sort(key=lambda x: x[0], reverse=True)
        row = candidates[0][1]
        return {
            "输入材料名": name,
            "匹配状态": "contains_candidate",
            "标准材料名": row.get("material_name", ""),
            "末级分类ID": row.get("category_id", ""),
            "末级分类名称": row.get("category_l3", ""),
            "分类路径": row.get("category_path", ""),
            "匹配依据": row.get("source", ""),
            "候选数量": len(candidates),
        }

    return {
        "输入材料名": name,
        "匹配状态": "unmatched",
        "标准材料名": "",
        "末级分类ID": "",
        "末级分类名称": "",
        "分类路径": "",
        "匹配依据": "",
        "候选数量": 0,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="材料名匹配到 CCE 三级分类")
    parser.add_argument("--材料库", dest="kb", default=str(默认材料库))
    parser.add_argument("--材料", dest="materials", nargs="*", default=[])
    parser.add_argument("--输入", dest="input", default="")
    parser.add_argument("--输出", dest="output", default=str(默认输出))
    args = parser.parse_args()

    names = 读取材料(args)
    if not names:
        raise RuntimeError("没有输入材料。请使用 --材料 或 --输入。")
    kb = 读JSON(Path(args.kb))
    rows = [匹配单个(name, kb) for name in names]
    写CSV(Path(args.output), rows, ["输入材料名", "匹配状态", "标准材料名", "末级分类ID", "末级分类名称", "分类路径", "匹配依据", "候选数量"])
    for row in rows:
        print(f"{row['输入材料名']} -> {row['标准材料名'] or '未命中'} | {row['分类路径'] or '-'} | {row['匹配状态']}")
    print(f"已输出: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
