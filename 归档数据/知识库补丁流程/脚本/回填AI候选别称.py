#!/usr/bin/env python3
"""将 AI三级分类别称候选.jsonl 中的别称直接回填为 AI回填CSV（跳过AI核验，按置信度分流）。"""
from __future__ import annotations

import argparse
import csv
import importlib.util
import json
from pathlib import Path


流程目录 = Path(__file__).resolve().parents[1]
数据目录 = 流程目录 / "数据"
默认候选JSONL = 数据目录 / "AI三级分类别称候选.jsonl"
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
    parser = argparse.ArgumentParser(description="从候选JSONL直接回填AI别称到知识库")
    parser.add_argument("--候选JSONL", default=str(默认候选JSONL))
    parser.add_argument("--输出CSV", default=str(默认AI回填CSV))
    parser.add_argument("--最小置信度", choices=["high", "medium", "low"], default="medium",
                        help="最低接受的置信度，低于此的标记为 low 待人工审核（默认 medium）")
    parser.add_argument("--允许低置信", action="store_true",
                        help="将低置信度别称也标记为 ai_candidate（默认只标记为 ai_candidate_low）")
    args = parser.parse_args()

    rows = []
    seen = set()
    stats = {"ok": 0, "no_alias": 0, "written_high": 0, "written_medium": 0, "written_low": 0}

    for task in ai_tools.读JSONL(Path(args.候选JSONL)):
        if task.get("status") != "ok":
            continue
        stats["ok"] += 1
        data = task.get("result") or {}
        category_id = str(data.get("category_id", "")).strip()
        category_path = str(data.get("category_path", "")).strip()

        alias_count = 0

        for row in data.get("category_aliases", []) or []:
            alias = str(row.get("alias", "")).strip()
            confidence = str(row.get("confidence", "medium")).strip()
            if not alias:
                continue
            key = (category_id, "", ai_tools.归一(alias))
            if key in seen:
                continue
            seen.add(key)
            source = "ai_candidate" if confidence != "low" else "ai_candidate_low"
            if confidence == "low" and not args.允许低置信:
                source = "ai_candidate_low"
            rows.append({
                "末级分类ID": category_id,
                "分类路径": category_path,
                "材料名称": data.get("category_l3", ""),
                "别称": alias,
                "来源": source,
                "备注": f"{confidence}: {row.get('reason', '')}",
            })
            if confidence == "high":
                stats["written_high"] += 1
            elif confidence == "medium":
                stats["written_medium"] += 1
            else:
                stats["written_low"] += 1
            alias_count += 1

        for material in data.get("materials", []) or []:
            material_name = str(material.get("material_name", "")).strip()
            for row in material.get("aliases", []) or []:
                alias = str(row.get("alias", "")).strip()
                confidence = str(row.get("confidence", "medium")).strip()
                if not alias:
                    continue
                key = (category_id, material_name, ai_tools.归一(alias))
                if key in seen:
                    continue
                seen.add(key)
                source = "ai_candidate" if confidence != "low" else "ai_candidate_low"
                if confidence == "low" and not args.允许低置信:
                    source = "ai_candidate_low"
                rows.append({
                    "末级分类ID": category_id,
                    "分类路径": category_path,
                    "材料名称": material_name,
                    "别称": alias,
                    "来源": source,
                    "备注": f"{confidence}: {row.get('reason', '')}",
                })
                if confidence == "high":
                    stats["written_high"] += 1
                elif confidence == "medium":
                    stats["written_medium"] += 1
                else:
                    stats["written_low"] += 1
                alias_count += 1

        if alias_count == 0:
            stats["no_alias"] += 1

    写CSV(Path(args.输出CSV), rows)
    print(f"候选JSONL任务: {stats['ok']} 个成功, {stats['no_alias']} 个无别名")
    print(f"已回填别称: {len(rows)} 条")
    print(f"  high:    {stats['written_high']}")
    print(f"  medium:  {stats['written_medium']}")
    print(f"  low:     {stats['written_low']}")
    print(f"输出: {args.输出CSV}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
