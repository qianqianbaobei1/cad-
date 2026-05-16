#!/usr/bin/env python3
"""R5: 从 T3 物料库自动构建 N3 规范库 + 参数-规范-版本追溯表。

提取：
  1. T3 中每个物料的标准代号（GB/GB/T/JGJ等）
  2. 规格模式JSON 中的参数定义（参数名→类型→单位→默认值）
  3. 分离标准代号和年份（如 GB/T 14902-2012 → 代号=GB/T 14902, 年份=2012）

输出：
  - 标准知识库/N3_规范库.json     — 完整规范库
  - 标准知识库/N3_参数追溯表.json  — 参数-规范-版本追溯
"""

from __future__ import annotations

import csv
import json
import re
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
T3_FJ_CSV = ROOT / "标准知识库" / "T3_房建_标准物料库.csv"
T3_AZ_CSV = ROOT / "标准知识库" / "T3_安装_标准物料库.csv"
N3_OUTPUT = ROOT / "标准知识库" / "N3_规范库.json"
N3_TRACE_OUTPUT = ROOT / "标准知识库" / "N3_参数追溯表.json"


def parse_standard_code(raw: str) -> dict:
    """解析标准代号，分离代号和年份。
    'GB/T 14902-2012' → {code: 'GB/T 14902', year: '2012', full: 'GB/T 14902-2012'}
    """
    raw = raw.strip()
    if not raw:
        return {}
    m = re.match(r"(.+?)[-—]\s*(\d{4})\s*(?:版)?$", raw)
    if m:
        return {"code": m.group(1).strip(), "year": m.group(2), "full": raw}
    return {"code": raw, "year": "", "full": raw}


def build_n3_from_t3() -> tuple[dict, dict]:
    """从 T3 提取规范定义和参数追溯。"""
    standards: dict[str, dict] = {}     # 标准代号 → 参数定义
    param_trace: dict[str, list] = defaultdict(list)  # 参数名 → [{material_id, standard, ...}]

    for csv_path in [T3_FJ_CSV, T3_AZ_CSV]:
        if not csv_path.exists():
            continue
        with open(csv_path, "r", encoding="utf-8-sig") as f:
            for row in csv.DictReader(f):
                mid = row.get("物料ID", "").strip()
                mat_name = row.get("标准名称", "").strip()
                std_raw = row.get("标准代号", "").strip()
                cat_path = row.get("品类展示路径", row.get("分类路径", "")).strip()
                unit = row.get("采购单位", "").strip()

                if not mid or not mat_name:
                    continue

                # 解析标准代号
                if std_raw:
                    # 可能包含多个标准，用 ; 或 , 分隔
                    for part in re.split(r"[;；,，]", std_raw):
                        parsed = parse_standard_code(part.strip())
                        if not parsed.get("code"):
                            continue
                        code = parsed["code"]
                        if code not in standards:
                            standards[code] = {
                                "code": code,
                                "year": parsed.get("year", ""),
                                "full": parsed.get("full", part.strip()),
                                "materials": [],
                                "params_defined": [],
                            }
                        if mid not in standards[code]["materials"]:
                            standards[code]["materials"].append(mid)

                # 解析规格模式 → 参数追溯
                spec_raw = row.get("规格模式JSON", "").strip()
                if spec_raw and spec_raw not in ("[]", "null", ""):
                    try:
                        patterns = json.loads(spec_raw)
                        if isinstance(patterns, list):
                            for p in patterns:
                                if not isinstance(p, dict):
                                    continue
                                pname = p.get("param", "").strip()
                                if not pname:
                                    continue
                                entry = {
                                    "material_id": mid,
                                    "material_name": mat_name,
                                    "category": cat_path,
                                    "unit": unit,
                                    "type": p.get("type", ""),
                                    "param_unit": p.get("unit", ""),
                                    "default": p.get("default", ""),
                                    "required": p.get("required", False),
                                    "options": p.get("options", ""),
                                    "standard_code": std_raw,
                                }
                                param_trace[pname].append(entry)
                                # Also record in standard definition (fix: match by parsed code)
                                for part2 in re.split(r"[;；,，]", std_raw):
                                    parsed2 = parse_standard_code(part2.strip())
                                    s_code = parsed2.get("code", "")
                                    if s_code and s_code in standards:
                                        if pname not in standards[s_code].setdefault("params_defined", []):
                                            standards[s_code]["params_defined"].append(pname)
                    except (json.JSONDecodeError, TypeError):
                        pass

    # Add material count
    for std in standards.values():
        std["material_count"] = len(std["materials"])
        std["param_count"] = len(std.get("params_defined", []))

    return standards, dict(param_trace)


def main():
    print("=" * 60)
    print("R5: 构建 N3 规范库 + 参数-规范-版本追溯表")
    print("=" * 60)

    print("\n[1/3] 从 T3 提取标准定义和参数...")
    standards, param_trace = build_n3_from_t3()

    n3_lib = {
        "version": "1.0.0",
        "generated_at": "2026-05-15",
        "source": "T3_房建_标准物料库.csv + T3_安装_标准物料库.csv",
        "standard_count": len(standards),
        "param_count": len(param_trace),
        "standards": standards,
    }

    n3_trace = {
        "version": "1.0.0",
        "generated_at": "2026-05-15",
        "description": "参数→规范版本追溯表。每个参数记录其在哪些T3物料中定义，关联标准代号。",
        "total_params": len(param_trace),
        "total_param_occurrences": sum(len(v) for v in param_trace.values()),
        "params": param_trace,
    }

    print(f"  标准规范: {len(standards)} 个")
    print(f"  参数定义: {len(param_trace)} 个参数, {n3_trace['total_param_occurrences']} 次出现")

    # Print top standards
    top = sorted(standards.values(), key=lambda x: x.get("param_count", 0), reverse=True)[:10]
    print(f"\n  Top 10 标准（按定义参数数）:")
    for s in top:
        print(f"    {s['full']:30s} | {s['param_count']:3d} 参数 | {s['material_count']:3d} 物料")

    print("\n[2/3] 保存 N3_规范库.json...")
    with open(N3_OUTPUT, "w", encoding="utf-8") as f:
        json.dump(n3_lib, f, ensure_ascii=False, indent=2)
    print(f"  → {N3_OUTPUT}")

    print("\n[3/3] 保存 N3_参数追溯表.json...")
    with open(N3_TRACE_OUTPUT, "w", encoding="utf-8") as f:
        json.dump(n3_trace, f, ensure_ascii=False, indent=2)
    print(f"  → {N3_TRACE_OUTPUT}")

    # Show param trace samples
    print("\n=== 参数追溯样本 ===")
    for pname, entries in list(param_trace.items())[:10]:
        e = entries[0]
        print(f"  {pname}: type={e['type']}, unit={e['param_unit']}, default={e['default']}, "
              f"material={e['material_id']}, std={e.get('standard_code','')}")

    print(f"\n✅ R5 完成：N3规范库 {len(standards)}条标准 + {len(param_trace)}个参数定义")


if __name__ == "__main__":
    main()
