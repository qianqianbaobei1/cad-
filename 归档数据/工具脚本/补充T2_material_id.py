#!/usr/bin/env python3
"""
T2 清单材料映射库 material_id 补充脚本。
五阶段流水线，复用 Q2 填充结果进行联合映射。
"""
import csv
import sys
import os
from pathlib import Path
from collections import defaultdict

sys.path.insert(0, str(Path(__file__).resolve().parent))

from material_id_utils import (
    BASE_DIR, Q2_PATH, T2_PATH, Q3_PATH, PROGRESS_PATH,
    clean_material_name, load_t3_library, build_t3_name_index,
    load_q3_mapping, build_q3_cleaned_index,
    build_internal_name_index,
    keyword_substring_match, load_progress, save_progress,
    read_csv, write_csv, print_stage_report,
)

DRY_RUN = '--dry-run' in sys.argv
SKIP_AI = '--skip-ai' in sys.argv or DRY_RUN

# T2 进度单独存
T2_PROGRESS_PATH = BASE_DIR / "过程数据/补充T2_material_id_progress.json"


def main():
    print("=" * 60)
    print("T2 material_id 补充脚本")
    if DRY_RUN:
        print(">>> DRY RUN 模式 — 不写入文件 <<<")
    print("=" * 60)

    # ── 1. 加载数据 ──
    print("\n[1/5] 加载数据...")
    t2_rows, t2_fields = read_csv(T2_PATH)
    print(f"  T2: {len(t2_rows)} 行, {len(t2_fields)} 字段")

    # 加载 Q2（已经过填充的）
    q2_rows, _ = read_csv(Q2_PATH)
    print(f"  Q2: {len(q2_rows)} 行（含已填充 material_id）")

    q3_data = load_q3_mapping()
    print(f"  Q3: {len(q3_data)} 条映射")

    t3_data = load_t3_library()
    print(f"  T3: {len(t3_data)} 标准物料")

    # 加载进度
    progress = load_progress()
    # 也加载 T2 专用进度
    t2_progress = {}
    if T2_PROGRESS_PATH.exists():
        import json
        with open(T2_PROGRESS_PATH, encoding='utf-8') as f:
            t2_progress = json.load(f)
    print(f"  已有进度: {len(t2_progress)} 条")

    # ── 2. 统计初始状态 ──
    total_rows = len(t2_rows)
    filled_before = sum(1 for r in t2_rows if r.get('material_id', '').strip())
    empty_before = total_rows - filled_before
    print(f"\n  初始状态: {filled_before}/{total_rows} 已填充 ({filled_before/total_rows*100:.1f}%)")
    print(f"  待填充: {empty_before}")

    # ── 3. 构建索引 ──
    print("\n[2/5] 构建索引...")

    # 内部 name→id（已填充行）
    filled_t2 = [r for r in t2_rows if r.get('material_id', '').strip()]
    t2_name_to_id, t2_ambiguous = build_internal_name_index(filled_t2, 'material_id', 'material_name_raw')
    print(f"  T2内部 name→id: {len(t2_name_to_id)} 个一致映射, {len(t2_ambiguous)} 个歧义")

    # Q2→id 映射：构建 {material_name_raw: material_id} from filled Q2 rows
    q2_name_to_id = {}
    for row in q2_rows:
        mid = row.get('material_id', '').strip()
        name = row.get('material_name_raw', '').strip()
        if mid and name:
            if name not in q2_name_to_id:
                q2_name_to_id[name] = set()
            q2_name_to_id[name].add(mid)
    # 只保留一致的
    q2_name_unique = {name: list(mids)[0] for name, mids in q2_name_to_id.items() if len(mids) == 1}
    print(f"  Q2 name→id: {len(q2_name_unique)} 个一致映射")

    # Q3 索引
    q3_cleaned = build_q3_cleaned_index(q3_data)
    print(f"  Q3 清洗后索引: {len(q3_cleaned)} 条")

    # T3 索引
    t3_std, t3_alias = build_t3_name_index(t3_data)
    print(f"  T3: {len(t3_std)} 标准名, {len(t3_alias)} 别名组")

    # 跨省份转移
    cross_province_map = defaultdict(lambda: defaultdict(set))
    for row in filled_t2:
        name = row['material_name_raw'].strip()
        mid = row['material_id'].strip()
        prov = row.get('province', '').strip()
        cleaned, quality = clean_material_name(name)
        if cleaned and quality >= 0.4 and mid:
            cross_province_map[cleaned.lower()][mid].add(prov)

    # ── 4. 运行各阶段 ──
    print("\n[3/5] 开始匹配流水线...")

    all_results = {}
    # 已有填充
    for i, row in enumerate(t2_rows):
        existing_mid = row.get('material_id', '').strip()
        if existing_mid:
            all_results[i] = {
                'material_id': existing_mid,
                'match_method': 'existing',
                'match_confidence': 1.00,
                'match_source': 'already_filled',
                'match_cleaned_name': '',
            }
        elif str(i) in t2_progress:
            all_results[i] = t2_progress[str(i)]

    stages = [
        ('t2_internal_name', 0.95, stage1_t2_internal),
        ('q2_joint_mapping', 0.85, stage2_q2_joint),
        ('t3_alias_exact', 0.85, stage3_t2_t3_alias),
        ('t2_cross_province', 0.75, stage4_t2_cross_province),
    ]

    cumulative_start = len(all_results)

    for stage_name, confidence, stage_func in stages:
        new_fills = stage_func(
            t2_rows, all_results, confidence,
            t2_name_to_id, t2_ambiguous,
            q2_name_unique, q2_name_to_id,
            q3_data, q3_cleaned,
            t3_data, t3_std, t3_alias,
            cross_province_map,
        )
        for idx, fill_info in new_fills.items():
            if idx not in all_results:
                all_results[idx] = fill_info

        hit_count = len(new_fills)
        empty_idxs = [i for i in range(total_rows) if i not in all_results]
        cumulative_hit = len(all_results)
        pct = cumulative_hit / total_rows * 100

        hit_samples = []
        for idx in list(new_fills.keys())[:5]:
            row = t2_rows[idx]
            info = new_fills[idx]
            hit_samples.append(f"{row['material_name_raw'][:50]} → {info['material_id']}")

        miss_samples = []
        for idx in empty_idxs[:5]:
            row = t2_rows[idx]
            miss_samples.append(f"{row['material_name_raw'][:50]} [{row.get('province','')}]")

        print_stage_report(
            f"T2-{stage_name} (conf={confidence})",
            hit_count, hit_count + len(empty_idxs),
            hit_samples, miss_samples
        )
        print(f"  累计覆盖率: {cumulative_hit}/{total_rows} ({pct:.1f}%)")

        if not DRY_RUN and hit_count > 0:
            _t2_autosave(t2_rows, t2_fields, all_results)

    # ── 5. AI 阶段 ──
    remaining = [i for i in range(total_rows) if i not in all_results]
    if remaining:
        print(f"\n[4/5] DeepSeek AI 推断 — {len(remaining)} 行待处理")
        if SKIP_AI:
            print(f"  [SKIP] 跳过 AI 阶段，{len(remaining)} 行保持未填充")
        else:
            ai_fill = stage5_t2_deepseek(t2_rows, remaining, all_results, t3_data)
            for idx, fill_info in ai_fill.items():
                all_results[idx] = fill_info
            if ai_fill:
                _t2_autosave(t2_rows, t2_fields, all_results)
    else:
        print(f"\n[4/5] DeepSeek AI 推断 — 无需运行，全部已填充")

    # ── 6. 最终统计 ──
    final_filled = len(all_results)
    final_pct = final_filled / total_rows * 100
    print(f"\n{'='*60}")
    print(f"最终结果: {final_filled}/{total_rows} ({final_pct:.1f}%)")

    method_counts = defaultdict(int)
    for info in all_results.values():
        method_counts[info.get('match_method', 'unknown')] += 1
    print("\n按匹配方法分布:")
    for method, count in sorted(method_counts.items(), key=lambda x: -x[1]):
        print(f"  {method}: {count} ({count/total_rows*100:.1f}%)")

    # ── 7. 写入 ──
    if not DRY_RUN:
        print("\n[5/5] 写入结果...")
        _t2_apply_and_write(t2_rows, t2_fields, all_results)
        import json
        T2_PROGRESS_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(T2_PROGRESS_PATH, 'w', encoding='utf-8') as f:
            json.dump({str(k): v for k, v in all_results.items()}, f, ensure_ascii=False, indent=2)
        print("  写入完成!")
    else:
        print("\n[DRY RUN] 跳过写入。使用以下命令实际运行:")
        print("  python3 工具脚本/补充T2_material_id.py")

    print("\n完成!")


# ── 阶段 1: T2 内部 name→id ──────────────────────────────

def stage1_t2_internal(t2_rows, all_results, confidence,
                       t2_name_to_id, t2_ambiguous,
                       q2_name_unique, q2_name_to_id,
                       q3_data, q3_cleaned,
                       t3_data, t3_std, t3_alias,
                       cross_province_map):
    """相同 material_name_raw 已唯一映射到 material_id"""
    new_fills = {}
    for i, row in enumerate(t2_rows):
        if i in all_results:
            continue
        name = row['material_name_raw'].strip()
        if name in t2_name_to_id:
            mid = t2_name_to_id[name]
            cleaned, _ = clean_material_name(name)
            new_fills[i] = {
                'material_id': mid,
                'match_method': 't2_internal_name',
                'match_confidence': confidence,
                'match_source': f't2_internal:{name[:40]}',
                'match_cleaned_name': cleaned,
            }
    return new_fills


# ── 阶段 2: Q2+Q3 联合映射 ──────────────────────────────

def stage2_q2_joint(t2_rows, all_results, confidence,
                    t2_name_to_id, t2_ambiguous,
                    q2_name_unique, q2_name_to_id,
                    q3_data, q3_cleaned,
                    t3_data, t3_std, t3_alias,
                    cross_province_map):
    """
    T2 中 name 精确匹配 Q2 中 name（Q2 已有 material_id 或通过高置信方法填充）。
    只使用 Q2 中置信度 >= 0.85 的填充结果。
    """
    new_fills = {}
    for i, row in enumerate(t2_rows):
        if i in all_results:
            continue
        name = row['material_name_raw'].strip()
        if not name:
            continue

        # 先查 Q2 精确 name→id
        if name in q2_name_unique:
            mid = q2_name_unique[name]
            cleaned, _ = clean_material_name(name)
            new_fills[i] = {
                'material_id': mid,
                'match_method': 'q2_joint_mapping',
                'match_confidence': confidence,
                'match_source': f'Q2:{name[:40]}',
                'match_cleaned_name': cleaned,
            }
            continue

        # 清洗后查 Q3
        cleaned, quality = clean_material_name(name)
        if quality < 0.4 or not cleaned:
            continue
        key = cleaned.lower()
        if key in q3_cleaned:
            mid = q3_cleaned[key]
            new_fills[i] = {
                'material_id': mid,
                'match_method': 'q2_joint_mapping',
                'match_confidence': confidence,
                'match_source': f'Q3-cleaned:{cleaned[:40]}',
                'match_cleaned_name': cleaned,
            }

    return new_fills


# ── 阶段 3: T3 别名精确匹配 ──────────────────────────────

def stage3_t2_t3_alias(t2_rows, all_results, confidence,
                       t2_name_to_id, t2_ambiguous,
                       q2_name_unique, q2_name_to_id,
                       q3_data, q3_cleaned,
                       t3_data, t3_std, t3_alias,
                       cross_province_map):
    """清洗后匹配 T3 别名或标准名"""
    new_fills = {}
    for i, row in enumerate(t2_rows):
        if i in all_results:
            continue
        name = row['material_name_raw'].strip()
        if not name:
            continue
        cleaned, quality = clean_material_name(name)
        if quality < 0.4 or not cleaned:
            continue
        key = cleaned.lower()

        alias_ids = t3_alias.get(key, set())
        std_id = t3_std.get(key)

        if len(alias_ids) == 1:
            mid = list(alias_ids)[0]
        elif std_id:
            mid = std_id
        elif len(alias_ids) > 1:
            continue
        else:
            continue

        t3_info = t3_data.get(mid, {})
        new_fills[i] = {
            'material_id': mid,
            'match_method': 't2_t3_alias_exact',
            'match_confidence': confidence,
            'match_source': f'T3:{t3_info.get("standard_name", mid)[:40]}',
            'match_cleaned_name': cleaned,
        }
    return new_fills


# ── 阶段 4: 跨省份转移 ──────────────────────────────────

def stage4_t2_cross_province(t2_rows, all_results, confidence,
                              t2_name_to_id, t2_ambiguous,
                              q2_name_unique, q2_name_to_id,
                              q3_data, q3_cleaned,
                              t3_data, t3_std, t3_alias,
                              cross_province_map):
    """同名材料在其他省份已填 material_id"""
    new_fills = {}
    for i, row in enumerate(t2_rows):
        if i in all_results:
            continue
        name = row['material_name_raw'].strip()
        if not name:
            continue
        cleaned, quality = clean_material_name(name)
        if quality < 0.4 or not cleaned:
            continue
        key = cleaned.lower()
        province = row.get('province', '').strip()

        if key not in cross_province_map:
            continue

        prov_mids = cross_province_map[key]
        candidates = [(mid, provs) for mid, provs in prov_mids.items()
                      if len(provs) >= 2 or province not in provs]
        if not candidates:
            continue

        if len(candidates) == 1:
            mid = candidates[0][0]
        else:
            candidates.sort(key=lambda x: -len(x[1]))
            if len(candidates[0][1]) > len(candidates[1][1]):
                mid = candidates[0][0]
            else:
                continue

        new_fills[i] = {
            'material_id': mid,
            'match_method': 't2_cross_province',
            'match_confidence': confidence,
            'match_source': f'cross-prov:{",".join(sorted(prov_mids[mid]))}',
            'match_cleaned_name': cleaned,
        }
    return new_fills


# ── 阶段 5: DeepSeek AI ──────────────────────────────────

def stage5_t2_deepseek(t2_rows, remaining, all_results, t3_data):
    """DeepSeek AI 推断 T2 material_id"""
    import os as _os
    import json as _json

    api_key = _os.environ.get('DEEPSEEK_API_KEY', '')
    if not api_key:
        env_path = BASE_DIR / '.env'
        if env_path.exists():
            with open(env_path) as f:
                for line in f:
                    line = line.strip()
                    if line.startswith('DEEPSEEK_API_KEY='):
                        api_key = line.split('=', 1)[1].strip()
                        break
    if not api_key:
        print("  [WARN] 未找到 DEEPSEEK_API_KEY，跳过 AI 阶段")
        return {}

    t3_ref = _build_t3_ref(t3_data)

    BATCH_SIZE = 20
    batches = [remaining[i:i+BATCH_SIZE] for i in range(0, len(remaining), BATCH_SIZE)]

    print(f"  共 {len(batches)} 批, 每批 {BATCH_SIZE} 行")

    new_fills = {}
    for batch_idx, batch in enumerate(batches):
        if batch_idx % 10 == 0 and batch_idx > 0:
            print(f"    进度: {batch_idx}/{len(batches)} 批, 命中 {len(new_fills)}")

        batch_rows = [(i, t2_rows[i]) for i in batch]
        result = _call_deepseek_batch(batch_rows, t3_ref, t3_data)

        for idx, mid_info in result.items():
            if mid_info and mid_info.get('material_id'):
                new_fills[idx] = {
                    'material_id': mid_info['material_id'],
                    'match_method': 't2_deepseek_ai',
                    'match_confidence': 0.50,
                    'match_source': f"DS:{mid_info.get('reason', '')[:40]}",
                    'match_cleaned_name': mid_info.get('cleaned_name', ''),
                }

        if not DRY_RUN and batch_idx % 5 == 4:
            pass  # autosave handled by caller

    return new_fills


def _build_t3_ref(t3_data):
    lines = []
    for mid, info in sorted(t3_data.items()):
        aliases_str = ', '.join(info['aliases'][:4])
        lines.append(f"{mid}: {info['standard_name']} (别名: {aliases_str}) [{info['category']}]")
    return '\n'.join(lines)


def _call_deepseek_batch(batch_rows, t3_ref, t3_data):
    import os as _os
    import json as _json

    items = []
    for idx, row in batch_rows:
        name = row.get('material_name_raw', '').strip()
        cleaned, _ = clean_material_name(name)
        quota_id = row.get('quota_id', '').strip()
        items.append(f"  [{idx}] 名称: {name} | 清洗后: {cleaned} | 定额: {quota_id}")

    items_text = '\n'.join(items)

    prompt = f"""你是建筑工程标准材料识别专家。请将以下清单材料映射到 T3 标准物料库。

T3 标准物料库:
{t3_ref}

待映射材料:
{items_text}

请对每行材料返回最匹配的 T3 material_id，或 null 如果无法确定。
返回 JSON 数组格式:
[{{"idx": 行号, "material_id": "MAT-XXX-XXX" 或 null, "reason": "简短理由", "cleaned_name": "清洗后名称"}}]

只返回 JSON 数组，不要其他内容。"""

    try:
        from openai import OpenAI
        client = OpenAI(
            api_key=_os.environ.get('DEEPSEEK_API_KEY', ''),
            base_url=_os.environ.get('DEEPSEEK_BASE_URL', 'https://api.deepseek.com'),
        )
        model = _os.environ.get('DEEPSEEK_MODEL', 'deepseek-v4-pro')

        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": "你是建筑工程标准材料识别专家。只返回 JSON 数组。"},
                {"role": "user", "content": prompt},
            ],
            temperature=0.1,
            max_tokens=4096,
        )

        raw = response.choices[0].message.content.strip()
        json_str = raw
        if '```json' in raw:
            json_str = raw.split('```json')[1].split('```')[0].strip()
        elif '```' in raw:
            json_str = raw.split('```')[1].split('```')[0].strip()

        results = _json.loads(json_str)
        result_map = {}
        for item in results:
            idx = item.get('idx')
            mid = item.get('material_id')
            if mid and mid in t3_data:
                result_map[idx] = {
                    'material_id': mid,
                    'reason': item.get('reason', ''),
                    'cleaned_name': item.get('cleaned_name', ''),
                }
        return result_map

    except Exception as e:
        print(f"  [ERROR] DeepSeek API 调用失败: {e}")
        return {}


# ── 写入辅助 ────────────────────────────────────────────────

def _t2_autosave(t2_rows, t2_fields, all_results):
    for i, row in enumerate(t2_rows):
        if i in all_results and not row.get('material_id', '').strip():
            row['material_id'] = all_results[i]['material_id']
    if t2_fields:
        write_csv(T2_PATH, t2_rows, t2_fields)
    import json
    T2_PROGRESS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(T2_PROGRESS_PATH, 'w', encoding='utf-8') as f:
        json.dump({str(k): v for k, v in all_results.items()}, f, ensure_ascii=False, indent=2)


def _t2_apply_and_write(t2_rows, t2_fields, all_results):
    for i, row in enumerate(t2_rows):
        if i in all_results:
            row['material_id'] = all_results[i]['material_id']
    write_csv(T2_PATH, t2_rows, t2_fields)


if __name__ == '__main__':
    main()
