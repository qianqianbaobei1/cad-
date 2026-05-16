#!/usr/bin/env python3
"""把 AI 核验通过的别称回填为独立 CSV。"""
from __future__ import annotations

import argparse
import csv
import importlib.util
from pathlib import Path


流程目录 = Path(__file__).resolve().parents[1]
数据目录 = 流程目录 / "数据"
默认核验JSONL = 数据目录 / "AI三级分类别称核验结果.jsonl"
默认AI回填CSV = 数据目录 / "三级分类材料别称AI回填.csv"

spec = importlib.util.spec_from_file_location("ai_tools", Path(__file__).with_name("通用AI工具.py"))
ai_tools = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(ai_tools)


def 写CSV(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = ["末级分类ID", "分类路径", "材料名称", "别称", "来源", "备注"]
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    parser = argparse.ArgumentParser(description="回填 AI 核验通过的三级分类材料别称")
    parser.add_argument("--核验JSONL", default=str(默认核验JSONL))
    parser.add_argument("--输出CSV", default=str(默认AI回填CSV))
    parser.add_argument("--允许低置信", action="store_true")
    args = parser.parse_args()

    allowed_conf = {"high", "medium"} | ({"low"} if args.允许低置信 else set())
    rows = []
    seen = set()
    for task in ai_tools.读JSONL(Path(args.核验JSONL)):
        if task.get("status") != "ok":
            continue
        category_id = str(task.get("category_id", "")).strip()
        category_path = str(task.get("category_path", "")).strip()
        for row in task.get("checked", []) or []:
            if row.get("decision") != "accept":
                continue
            if row.get("confidence") not in allowed_conf:
                continue
            material_name = str(row.get("target_name", "")).strip()
            alias = str(row.get("alias", "")).strip()
            if not category_id or not alias:
                continue
            key = (category_id, material_name, ai_tools.归一(alias))
            if key in seen:
                continue
            seen.add(key)
            rows.append({
                "末级分类ID": category_id,
                "分类路径": category_path,
                "材料名称": material_name,
                "别称": alias,
                "来源": "ai_verified",
                "备注": f"{row.get('confidence', '')}: {row.get('reason', '')}",
            })

    写CSV(Path(args.输出CSV), rows)
    print(f"已回填 AI 核验别称: {len(rows)} 条 -> {args.输出CSV}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
