#!/usr/bin/env python3
"""知识库同步脚本 — 从主知识库同步 CSV/JSON 数据到本地执行平台的标准知识库

用法:
  python3 工具脚本/同步外部知识库.py              # 全量同步
  python3 工具脚本/同步外部知识库.py --check       # 仅检查差异
  python3 工具脚本/同步外部知识库.py --rebuild     # 重建所有索引
"""
import argparse
import csv
import json
import shutil
import sys
import os
from pathlib import Path
from datetime import datetime

ROOT = Path(__file__).resolve().parent.parent
KB_SOURCE = Path("/Users/qianqianawodebaobei/Desktop/智能清单/知识库")
STANDARD_KB = ROOT / "标准知识库" / "源数据"
INDEX_DIR = ROOT / "项目数据" / "本地知识库包" / "索引"

sys.path.insert(0, str(ROOT / "工具脚本"))
from kb_gen_config import CSV_SOURCES, INDEX_DEFINITIONS, get_source_path, list_source_status


# ── 文件同步映射 ──
SYNC_MAP: list[tuple[str, str, str]] = [
    # (源子目录, 源文件名, 目标子目录)
    # 定额库
    ("01_定额库", "Q0_清单项目编码_房建工程.csv", "01_定额库"),
    ("01_定额库", "Q0_清单项目编码_房建工程_v2.csv", "01_定额库"),
    ("01_定额库", "Q0_清单项目编码_安装工程.csv", "01_定额库"),
    ("01_定额库", "Q1_定额索引.csv", "01_定额库"),
    ("01_定额库", "Q2_定额材料消耗.csv", "01_定额库"),
    ("01_定额库", "Q3_定额材料映射.csv", "01_定额库"),
    ("01_定额库", "T2_清单材料映射库.csv", "01_定额库"),
    # 房屋建筑
    ("01_房屋建筑与装饰工程/CSV导出", "01_t1_分部代码路由.csv", "01_房屋建筑与装饰工程/CSV导出"),
    ("01_房屋建筑与装饰工程/CSV导出", "02_t2_清单材料映射.csv", "01_房屋建筑与装饰工程/CSV导出"),
    ("01_房屋建筑与装饰工程/CSV导出", "03_t3_标准物料库.csv", "01_房屋建筑与装饰工程/CSV导出"),
    # 通用安装
    ("02_通用安装工程/CSV导出", "01_t1_分部代码路由.csv", "02_通用安装工程/CSV导出"),
    ("02_通用安装工程/CSV导出", "02_t3_标准物料库.csv", "02_通用安装工程/CSV导出"),
    ("02_通用安装工程/CSV导出", "03_t2_清单材料映射.csv", "02_通用安装工程/CSV导出"),
    # 国家规范库
    ("03_国家规范库", "N1_国家规范索引.csv", "03_国家规范库"),
    ("03_国家规范库", "N2_规范条文库.csv", "03_国家规范库"),
    ("03_国家规范库", "N3_材料技术参数定义.csv", "03_国家规范库"),
    ("03_国家规范库", "N4_材料参数取值.csv", "03_国家规范库"),
    ("03_国家规范库", "N5_材料规范映射.csv", "03_国家规范库"),
    ("03_国家规范库", "N6_规范校验规则.csv", "03_国家规范库"),
    # 品类树
    ("06_品类树", "品类节点索引.csv", "06_品类树"),
    ("06_品类树", "T3_品类映射.csv", "06_品类树"),
    ("06_品类树", "品类树_完整.json", "06_品类树"),
    # 品类树损耗率
    ("05_品类树损耗率", "品类树_含属性值_补实际损耗率.json", "05_品类树损耗率"),
    ("05_品类树损耗率", "损耗率覆盖明细.csv", "05_品类树损耗率"),
    # 省份适配
    ("省份适配/CSV导出", "06_province_config.csv", "省份适配/CSV导出"),
    ("省份适配/CSV导出", "07_province_material_rules.csv", "省份适配/CSV导出"),
    ("省份适配/CSV导出", "08_province_param_overrides.csv", "省份适配/CSV导出"),
    ("省份适配/CSV导出", "09_bj_t5_dimensions.csv", "省份适配/CSV导出"),
]


def read_csv(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with open(path, encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def write_csv(path: Path, rows: list[dict], fieldnames: list[str]):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def sync_files(check_only: bool = False) -> dict:
    """同步CSV/JSON文件从主知识库到标准知识库"""
    results = {"synced": [], "skipped": [], "missing_source": [], "errors": []}

    for src_subdir, src_filename, dst_subdir in SYNC_MAP:
        src_path = KB_SOURCE / src_subdir / src_filename
        dst_path = STANDARD_KB / dst_subdir / src_filename

        if not src_path.exists():
            results["missing_source"].append(str(src_path))
            continue

        dst_path.parent.mkdir(parents=True, exist_ok=True)

        if dst_path.exists():
            src_mtime = src_path.stat().st_mtime
            dst_mtime = dst_path.stat().st_mtime
            if src_mtime <= dst_mtime:
                results["skipped"].append(str(dst_path))
                continue

        if check_only:
            results["synced"].append(f"(dry-run) {dst_path}")
        else:
            try:
                shutil.copy2(src_path, dst_path)
                results["synced"].append(str(dst_path))
            except Exception as e:
                results["errors"].append(f"{dst_path}: {e}")

    return results


def build_indexes() -> dict:
    """根据 INDEX_DEFINITIONS 重建 JSON 索引"""
    INDEX_DIR.mkdir(parents=True, exist_ok=True)
    built = {}

    for idx_name, idx_def in INDEX_DEFINITIONS.items():
        source_name = idx_def["source"]
        key_field = idx_def["key_field"]
        value_mode = idx_def.get("value_mode", "single")  # single or list
        src_path = get_source_path(source_name)

        if not src_path:
            built[idx_name] = {"error": f"源表 {source_name} 未找到"}
            continue

        rows = read_csv(src_path)
        if not rows:
            built[idx_name] = {"error": f"源表 {source_name} 为空"}
            continue

        index = {}
        for r in rows:
            key = r.get(key_field, "").strip()
            if not key:
                continue
            if value_mode == "list":
                index.setdefault(key, []).append(r)
            else:
                index[key] = r

        out_path = INDEX_DIR / f"{idx_name}.json"
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(index, f, ensure_ascii=False, indent=2)

        built[idx_name] = {
            "keys": len(index),
            "rows": sum(len(v) for v in index.values()) if value_mode == "list" else len(index),
            "path": str(out_path),
        }

    return built


def check_integrity() -> dict:
    """检查数据完整性"""
    from kb_gen_config import REQUIRED_COLUMNS
    issues = {}

    for table_name, required_cols in REQUIRED_COLUMNS.items():
        path = get_source_path(table_name)
        if not path:
            issues[table_name] = {"error": "源文件不存在"}
            continue
        rows = read_csv(path)
        if not rows:
            issues[table_name] = {"error": "源文件为空"}
            continue

        actual = list(rows[0].keys())
        missing = [c for c in required_cols if c not in actual]
        if missing:
            issues[table_name] = {
                "warning": f"缺失字段: {missing}",
                "row_count": len(rows),
            }
        else:
            issues[table_name] = {"ok": True, "row_count": len(rows)}

    return issues


def print_summary(results: dict, title: str = ""):
    if title:
        print(f"\n{'='*50}\n  {title}\n{'='*50}")
    for key, val in results.items():
        if isinstance(val, list):
            print(f"  {key}: {len(val)} 项")
            for item in val[:5]:
                print(f"    - {item}")
            if len(val) > 5:
                print(f"    ... 还有 {len(val)-5} 项")
        elif isinstance(val, dict):
            print(f"  {key}:")
            for k, v in val.items():
                print(f"    {k}: {v}")
        else:
            print(f"  {key}: {val}")


def main():
    parser = argparse.ArgumentParser(description="知识库同步工具")
    parser.add_argument("--check", action="store_true", help="仅检查差异，不实际同步")
    parser.add_argument("--rebuild", action="store_true", help="仅重建索引")
    parser.add_argument("--sync-only", action="store_true", help="仅同步文件，不建索引")
    parser.add_argument("--integrity", action="store_true", help="检查数据完整性")
    args = parser.parse_args()

    print(f"知识库同步工具")
    print(f"  源: {KB_SOURCE}")
    print(f"  目标: {STANDARD_KB}")
    print(f"  索引: {INDEX_DIR}")

    if args.integrity:
        integrity = check_integrity()
        print_summary(integrity, "数据完整性检查")
        return

    if args.rebuild:
        print("\n>>> 重建索引...")
        built = build_indexes()
        print_summary(built, "索引重建结果")
        return

    if not args.sync_only:
        print("\n>>> 检查数据源状态...")
        source_status = list_source_status()
        missing_sources = [k for k, v in source_status.items() if not v["found"]]
        print(f"  已找到: {sum(1 for v in source_status.values() if v['found'])}/{len(source_status)}")
        if missing_sources:
            print(f"  缺失: {missing_sources}")

    print(f"\n>>> {'[DRY RUN] ' if args.check else ''}同步文件...")
    sync_results = sync_files(check_only=args.check)
    print_summary(sync_results, "文件同步结果")

    if not args.check and not args.sync_only:
        print("\n>>> 重建索引...")
        built = build_indexes()
        print_summary(built, "索引重建结果")

    print("\n完成!")


if __name__ == "__main__":
    main()
