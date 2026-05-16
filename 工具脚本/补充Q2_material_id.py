#!/usr/bin/env python3
"""
Q2 material_id 多阶段补充脚本。
基于 material_id_utils 的共享函数，8阶段逐级匹配填充空 material_id。
已有 material_id 的行永不覆盖（支持中断续跑）。

阶段:
  1. 内部 code→id (0.95) — 已填行中 material_code→material_id 一致映射
  2. 内部 name→id (0.95) — 已填行中 material_name_raw→material_id 一致映射
  3. Q3 精确匹配 (0.90) — Q2.材料名 == Q3.材料名
  4. 清洗后 Q3 匹配 (0.85) — 双方清洗后精确匹配
  5. T3 别名精确 (0.85) — 清洗后匹配 T3 别名/标准名
  6. T3 关键词+子串 (0.70) — 关键词命中+排除检查
  7. 跨省份转移 (0.75) — 同名不同省已填，直接复制
  8. DeepSeek AI 推断 (0.50) — 批量送 AI（可选）

用法:
  python3 工具脚本/补充Q2_material_id.py --dry-run     # 预估
  python3 工具脚本/补充Q2_material_id.py                # 执行阶段1-7
  python3 工具脚本/补充Q2_material_id.py --stages 1-5   # 仅指定阶段
  python3 工具脚本/补充Q2_material_id.py --stage8       # 运行AI阶段
"""
from __future__ import annotations

import argparse
import csv
import os
import sys
import time
from collections import defaultdict
from pathlib import Path

# 确保能 import material_id_utils
TOOL_DIR = Path(__file__).resolve().parent
if str(TOOL_DIR) not in sys.path:
    sys.path.insert(0, str(TOOL_DIR))

from material_id_utils import (
    clean_material_name, load_t3_library, build_t3_name_index,
    load_q3_mapping, build_q3_cleaned_index,
    build_internal_code_index, build_internal_name_index,
    keyword_substring_match,
    load_progress, save_progress,
    read_csv, write_csv,
    print_stage_report, cross_validate,
    Q2_PATH, T2_PATH, Q3_PATH, PROGRESS_PATH,
)

ROOT = Path(__file__).resolve().parents[1]
OUTPUT_PATH = Q2_PATH  # 原地更新
BACKUP_DIR = ROOT / "过程数据" / "分类库备份"
LOG_PATH = ROOT / "过程数据" / "补充Q2_material_id_log.jsonl"

# 新增输出列
NEW_COLS = ["match_method", "match_confidence", "match_source", "match_cleaned_name"]


def load_and_prepare():
    """加载 Q2 数据并分离已填/待填"""
    rows, fieldnames = read_csv(Q2_PATH)
    # 确保新列存在
    for col in NEW_COLS:
        if col not in fieldnames:
            fieldnames.append(col)

    filled = 0
    empty = 0
    empty_rows = []
    for i, row in enumerate(rows):
        for col in NEW_COLS:
            if col not in row:
                row[col] = ""
        if row.get("material_id", "").strip():
            filled += 1
        else:
            empty += 1
            empty_rows.append(i)

    print(f"Q2 总行数: {len(rows)}, 已填: {filled} ({100*filled/max(len(rows),1):.1f}%), 待填: {empty}")
    return rows, fieldnames, empty_rows


def stage1_internal_code(rows, empty_indices):
    """阶段1: material_code → material_id 内部映射"""
    code_index = build_internal_code_index(rows, id_col="material_id", code_col="material_code")
    print(f"  内部code索引: {len(code_index)} 条一致映射")

    hit, samples_hit, samples_miss = 0, [], []
    for idx in empty_indices:
        row = rows[idx]
        if row.get("material_id", "").strip():
            continue
        code = row.get("material_code", "").strip()
        mid = code_index.get(code)
        if mid:
            row["material_id"] = mid
            row["match_method"] = "internal_code_to_id"
            row["match_confidence"] = "0.95"
            row["match_source"] = f"code:{code}"
            hit += 1
            if len(samples_hit) < 5:
                samples_hit.append(f"{code} → {mid} ({row.get('material_name_raw','')[:30]})")
        else:
            if len(samples_miss) < 5:
                samples_miss.append(f"{code} ({row.get('material_name_raw','')[:30]})")

    print_stage_report("Stage1 内部code→id", hit, len(empty_indices), samples_hit, samples_miss)
    return hit


def stage2_internal_name(rows, empty_indices):
    """阶段2: material_name_raw → material_id 内部映射"""
    name_index, ambiguous = build_internal_name_index(rows, id_col="material_id", name_col="material_name_raw")
    print(f"  内部name索引: {len(name_index)} 条 (排除{len(ambiguous)}条歧义)")

    hit, samples_hit, samples_miss = 0, [], []
    for idx in empty_indices:
        row = rows[idx]
        if row.get("material_id", "").strip():
            continue
        name = row.get("material_name_raw", "").strip()
        mid = name_index.get(name)
        if mid:
            row["material_id"] = mid
            row["match_method"] = "internal_name_to_id"
            row["match_confidence"] = "0.95"
            row["match_source"] = f"name_exact:{name[:50]}"
            hit += 1
            if len(samples_hit) < 5:
                samples_hit.append(f"'{name[:40]}' → {mid}")
        else:
            if name in ambiguous and len(samples_miss) < 5:
                samples_miss.append(f"歧义: '{name[:40]}'")

    print_stage_report("Stage2 内部name→id", hit, len(empty_indices), samples_hit, samples_miss)
    return hit


def stage3_q3_exact(rows, empty_indices, q3_data):
    """阶段3: Q3 精确匹配 — Q2.材料名 == Q3.材料名"""
    hit, samples_hit, samples_miss = 0, [], []
    for idx in empty_indices:
        row = rows[idx]
        if row.get("material_id", "").strip():
            continue
        name = row.get("material_name_raw", "").strip()
        if name in q3_data:
            row["material_id"] = q3_data[name]["material_id"]
            row["match_method"] = "q3_exact_match"
            row["match_confidence"] = "0.90"
            row["match_source"] = f"Q3:{q3_data[name].get('mapping_id','')}"
            hit += 1
            if len(samples_hit) < 5:
                samples_hit.append(f"'{name[:40]}' → {q3_data[name]['material_id']}")

    print_stage_report("Stage3 Q3精确匹配", hit, len(empty_indices), samples_hit, samples_miss)
    return hit


def stage4_q3_cleaned(rows, empty_indices, q3_cleaned):
    """阶段4: 清洗后 Q3 匹配"""
    hit, samples_hit, samples_miss = 0, [], []
    for idx in empty_indices:
        row = rows[idx]
        if row.get("material_id", "").strip():
            continue
        name = row.get("material_name_raw", "").strip()
        cleaned, quality = clean_material_name(name)
        if cleaned and quality >= 0.4 and len(cleaned) >= 3:
            key = cleaned.lower()
            mid = q3_cleaned.get(key)
            if mid:
                row["material_id"] = mid
                row["match_method"] = "q3_cleaned_match"
                row["match_confidence"] = "0.85"
                row["match_source"] = f"Q3_cleaned:{cleaned[:60]}"
                row["match_cleaned_name"] = cleaned[:100]
                hit += 1
                if len(samples_hit) < 5:
                    samples_hit.append(f"'{cleaned[:40]}' → {mid}")
            elif len(samples_miss) < 5:
                samples_miss.append(f"'{cleaned[:40]}' (q={quality:.1f})")

    print_stage_report("Stage4 清洗后Q3匹配", hit, len(empty_indices), samples_hit, samples_miss)
    return hit


def stage5_t3_alias(rows, empty_indices, t3_std_idx, t3_alias_idx):
    """阶段5: T3 别名/标准名 精确匹配（清洗后）"""
    hit, samples_hit, samples_miss = 0, [], []
    for idx in empty_indices:
        row = rows[idx]
        if row.get("material_id", "").strip():
            continue
        name = row.get("material_name_raw", "").strip()
        cleaned, quality = clean_material_name(name)
        if not cleaned or quality < 0.4 or len(cleaned) < 2:
            continue

        key = cleaned.lower()
        mid = None
        source = ""

        # 先查标准名
        if key in t3_std_idx:
            mid = t3_std_idx[key]
            source = f"T3_std:{cleaned[:60]}"
        # 再查别名
        elif key in t3_alias_idx:
            ids = t3_alias_idx[key]
            if len(ids) == 1:
                mid = list(ids)[0]
                source = f"T3_alias:{cleaned[:60]}"

        if mid:
            row["material_id"] = mid
            row["match_method"] = "t3_alias_exact"
            row["match_confidence"] = "0.85"
            row["match_source"] = source
            row["match_cleaned_name"] = cleaned[:100]
            hit += 1
            if len(samples_hit) < 5:
                samples_hit.append(f"'{cleaned[:40]}' → {mid}")

    print_stage_report("Stage5 T3别名精确", hit, len(empty_indices), samples_hit, samples_miss)
    return hit


def stage6_t3_keyword(rows, empty_indices, t3_data):
    """阶段6: T3 关键词+子串匹配"""
    hit, samples_hit, samples_miss = 0, [], []
    empty_count = len(empty_indices)

    for idx in empty_indices:
        row = rows[idx]
        if row.get("material_id", "").strip():
            continue
        name = row.get("material_name_raw", "").strip()
        cleaned, quality = clean_material_name(name)
        if not cleaned or quality < 0.4 or len(cleaned) < 2:
            continue

        mid = keyword_substring_match(cleaned, t3_data)
        if mid:
            row["material_id"] = mid
            row["match_method"] = "t3_keyword_substring"
            row["match_confidence"] = "0.70"
            row["match_source"] = f"T3_kw:{cleaned[:60]}"
            row["match_cleaned_name"] = cleaned[:100]
            hit += 1
            if len(samples_hit) < 5:
                samples_hit.append(f"'{cleaned[:40]}' → {mid}")
        elif len(samples_miss) < 5:
            samples_miss.append(f"'{cleaned[:40]}' (q={quality:.1f})")

    print_stage_report(f"Stage6 T3关键词+子串", hit, empty_count, samples_hit, samples_miss)
    return hit


def stage7_cross_province(rows, empty_indices):
    """阶段7: 跨省份转移 — 同名不同省已填，复制 material_id"""
    # 按 material_name_raw 分组，收集已填的 material_id
    name_to_ids = defaultdict(set)
    for row in rows:
        mid = row.get("material_id", "").strip()
        name = row.get("material_name_raw", "").strip()
        if mid and name:
            name_to_ids[name].add(mid)

    # 只保留一致的
    name_map = {name: list(ids)[0] for name, ids in name_to_ids.items() if len(ids) == 1}

    hit, samples_hit = 0, []
    for idx in empty_indices:
        row = rows[idx]
        if row.get("material_id", "").strip():
            continue
        name = row.get("material_name_raw", "").strip()
        mid = name_map.get(name)
        if mid:
            row["material_id"] = mid
            row["match_method"] = "cross_province_transfer"
            row["match_confidence"] = "0.75"
            row["match_source"] = f"cross_province:{name[:50]}"
            hit += 1
            if len(samples_hit) < 5:
                samples_hit.append(f"'{name[:40]}' → {mid}")

    print_stage_report("Stage7 跨省份转移", hit, len(empty_indices), samples_hit, [])
    return hit


def count_filled(rows):
    return sum(1 for r in rows if r.get("material_id", "").strip())


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="仅预估不写入")
    parser.add_argument("--stages", type=str, default="1-7", help="要运行的阶段，如 '1-7' 或 '1-5'")
    parser.add_argument("--stage8", action="store_true", help="运行 AI 阶段（需API）")
    parser.add_argument("--skip", type=int, default=0, help="跳过前N行（续跑用）")
    args = parser.parse_args()

    # 解析阶段范围
    stage_range = args.stages.split("-")
    start_stage = int(stage_range[0])
    end_stage = int(stage_range[-1])

    print("=" * 60)
    print(f"Q2 material_id 多阶段补充 (Stage {start_stage}-{end_stage})")
    print("=" * 60)

    t0 = time.time()

    # 加载数据
    rows, fieldnames, empty_indices = load_and_prepare()
    if args.skip > 0:
        empty_indices = [i for i in empty_indices if i >= args.skip]
        print(f"跳过前 {args.skip} 行，剩余待填索引: {len(empty_indices)}")

    empty_before = len([i for i in empty_indices if not rows[i].get("material_id", "").strip()])
    print(f"当前待填: {empty_before}\n")

    if args.dry_run:
        print("[DRY RUN] 不写入数据\n")
        # 只做轻量统计
        code_index = build_internal_code_index(rows, id_col="material_id", code_col="material_code")
        name_index, ambig = build_internal_name_index(rows, id_col="material_id", name_col="material_name_raw")
        q3_data = load_q3_mapping()
        print(f"Stage1 内部code索引: {len(code_index)} 条")
        print(f"Stage2 内部name索引: {len(name_index)} 条 (歧义{len(ambig)})")
        print(f"Stage3 Q3映射: {len(q3_data)} 条")
        print(f"Stage7 跨省份候选: 待分析")
        # 粗略估计
        code_hits = sum(1 for i in empty_indices if rows[i].get("material_code","").strip() in code_index)
        print(f"\n预估 Stage1 命中: ~{code_hits}")
        print("运行 --stage8 前请先完成 Stage1-7")
        return 0

    # 加载外部索引（按需）
    q3_data = None
    q3_cleaned = None
    t3_data = None
    t3_std_idx = None
    t3_alias_idx = None

    if start_stage <= 3 <= end_stage:
        print("加载 Q3 映射...")
        q3_data = load_q3_mapping()
        print(f"  Q3 条目: {len(q3_data)}")

    if start_stage <= 4 <= end_stage:
        if q3_data is None:
            q3_data = load_q3_mapping()
        print("构建 Q3 清洗索引...")
        q3_cleaned = build_q3_cleaned_index(q3_data)
        print(f"  Q3清洗索引: {len(q3_cleaned)} 条")

    if start_stage <= 5 <= end_stage or start_stage <= 6 <= end_stage:
        print("加载 T3 物料库...")
        t3_data = load_t3_library()
        print(f"  T3 物料: {len(t3_data)}")
        if start_stage <= 5 <= end_stage:
            t3_std_idx, t3_alias_idx = build_t3_name_index(t3_data)
            print(f"  T3 标准名索引: {len(t3_std_idx)}, 别名索引: {len(t3_alias_idx)}")

    # 备份
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    import shutil
    backup_path = BACKUP_DIR / f"Q2_backup_{int(time.time())}.csv"
    shutil.copy2(Q2_PATH, backup_path)
    print(f"\n已备份: {backup_path}\n")

    total_hit = 0
    log_fh = open(LOG_PATH, "a", encoding="utf-8")

    # ── 阶段执行 ──
    stage_funcs = [
        (1, stage1_internal_code, (rows, empty_indices)),
        (2, stage2_internal_name, (rows, empty_indices)),
        (3, stage3_q3_exact, (rows, empty_indices, q3_data)),
        (4, stage4_q3_cleaned, (rows, empty_indices, q3_cleaned)),
        (5, stage5_t3_alias, (rows, empty_indices, t3_std_idx, t3_alias_idx)),
        (6, stage6_t3_keyword, (rows, empty_indices, t3_data)),
        (7, stage7_cross_province, (rows, empty_indices)),
    ]

    for stage_num, func, func_args in stage_funcs:
        if start_stage <= stage_num <= end_stage:
            hit = func(*func_args)
            total_hit += hit
            if hit > 0:
                write_csv(Q2_PATH, rows, fieldnames)
                print(f"  → 已保存 {hit} 条")

            log_entry = {
                "timestamp": time.time(),
                "stage": stage_num,
                "hits": hit,
                "total_filled": count_filled(rows),
            }
            log_fh.write(json.dumps(log_entry, ensure_ascii=False) + "\n")
            log_fh.flush()

    log_fh.close()

    # 最终统计
    final_filled = count_filled(rows)
    pct = final_filled / max(len(rows), 1) * 100
    print(f"\n{'='*60}")
    print(f"完成! Stage {start_stage}-{end_stage} 共填充: {total_hit}")
    print(f"覆盖率: {final_filled}/{len(rows)} ({pct:.1f}%)")
    print(f"耗时: {time.time()-t0:.0f}s")
    print(f"日志: {LOG_PATH}")

    # AI阶段提示
    if end_stage >= 7 and not args.stage8:
        remaining = len([i for i in empty_indices if not rows[i].get("material_id", "").strip()])
        if remaining > 0:
            print(f"\n还有 {remaining} 条未填充。可运行 --stage8 使用 AI 推断")


if __name__ == "__main__":
    import json
    import shutil
    main()
