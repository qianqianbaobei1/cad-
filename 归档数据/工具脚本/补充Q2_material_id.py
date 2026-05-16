#!/usr/bin/env python3
"""
Q2 定额材料消耗 material_id 补充脚本。
八阶段流水线，精度优先于召回率。
"""
import csv
import sys
import os
from pathlib import Path
from collections import defaultdict

# 确保能 import 同目录工具
sys.path.insert(0, str(Path(__file__).resolve().parent))

from material_id_utils import (
    BASE_DIR, Q2_PATH, Q3_PATH, Q2_INDEX_PATH, PROGRESS_PATH,
    clean_material_name, load_t3_library, build_t3_name_index,
    load_q3_mapping, build_q3_cleaned_index,
    build_internal_code_index, build_internal_name_index,
    keyword_substring_match, load_progress, save_progress,
    read_csv, write_csv, print_stage_report, cross_validate,
)

# ── 配置 ────────────────────────────────────────────────────
DRY_RUN = '--dry-run' in sys.argv
SKIP_AI = '--skip-ai' in sys.argv or DRY_RUN
SAVE_EVERY = 10000  # 每填充 N 行自动保存一次
PROVINCES = ['SX', 'HN', 'HB', 'BJ', 'GD']


def main():
    print("=" * 60)
    print("Q2 material_id 补充脚本")
    if DRY_RUN:
        print(">>> DRY RUN 模式 — 不写入文件 <<<")
    print("=" * 60)

    # ── 1. 加载数据 ──
    print("\n[1/8] 加载数据...")
    q2_rows, q2_fields = read_csv(Q2_PATH)
    print(f"  Q2: {len(q2_rows)} 行, {len(q2_fields)} 字段")

    q3_data = load_q3_mapping()
    print(f"  Q3: {len(q3_data)} 条映射")

    t3_data = load_t3_library()
    print(f"  T3: {len(t3_data)} 标准物料")

    # 加载现有进度
    progress = load_progress()
    print(f"  已有进度: {len(progress)} 条")

    # ── 2. 统计初始状态 ──
    total_rows = len(q2_rows)
    filled_before = sum(1 for r in q2_rows if r.get('material_id', '').strip())
    empty_before = total_rows - filled_before
    print(f"\n  初始状态: {filled_before}/{total_rows} 已填充 ({filled_before/total_rows*100:.1f}%)")
    print(f"  待填充: {empty_before}")

    # ── 3. 构建索引 ──
    print("\n[2/8] 构建索引...")

    # 内部 code→id 索引（只从已填充的行）
    filled_rows = [r for r in q2_rows if r.get('material_id', '').strip()]
    code_to_id = build_internal_code_index(filled_rows, 'material_id', 'material_code')
    print(f"  内部 code→id: {len(code_to_id)} 个一致映射")

    # 内部 name→id 索引
    name_to_id, ambiguous_names = build_internal_name_index(filled_rows, 'material_id', 'material_name_raw')
    print(f"  内部 name→id: {len(name_to_id)} 个一致映射, {len(ambiguous_names)} 个歧义排除")

    # Q3 精确索引
    q3_cleaned = build_q3_cleaned_index(q3_data)
    print(f"  Q3 清洗后索引: {len(q3_cleaned)} 条")

    # T3 索引
    t3_std, t3_alias = build_t3_name_index(t3_data)
    print(f"  T3 标准名索引: {len(t3_std)} 条, 别名索引: {len(t3_alias)} 条")

    # 跨省份映射：{cleaned_name: {material_id: set of provinces}}
    # 用于阶段7
    cross_province_map = defaultdict(lambda: defaultdict(set))
    for row in filled_rows:
        name = row['material_name_raw'].strip()
        mid = row['material_id'].strip()
        prov = row.get('province', '').strip()
        cleaned, quality = clean_material_name(name)
        if cleaned and quality >= 0.4 and mid:
            cross_province_map[cleaned.lower()][mid].add(prov)

    # ── 4. 运行各阶段 ──
    print("\n[3/8] 开始匹配流水线...")

    stages = [
        ('internal_code_to_id', 0.95, stage1_internal_code),
        ('internal_name_to_id', 0.95, stage2_internal_name),
        ('q3_exact_match', 0.90, stage3_q3_exact),
        ('q3_cleaned_match', 0.85, stage4_q3_cleaned),
        ('t3_alias_exact', 0.85, stage5_t3_alias),
        ('t3_keyword_substring', 0.70, stage6_t3_keyword),
        ('cross_province_transfer', 0.75, stage7_cross_province),
    ]

    all_results = {}  # row_index → fill_info
    # 先把已有填充和进度加入
    for i, row in enumerate(q2_rows):
        existing_mid = row.get('material_id', '').strip()
        if existing_mid:
            all_results[i] = {
                'material_id': existing_mid,
                'match_method': 'existing',
                'match_confidence': 1.00,
                'match_source': 'already_filled',
                'match_cleaned_name': '',
            }
        elif str(i) in progress:
            all_results[i] = progress[str(i)]

    cumulative_start = len(all_results)
    print(f"  已有数据: {cumulative_start} 行")

    for stage_name, confidence, stage_func in stages:
        new_fills = stage_func(
            q2_rows, all_results, confidence,
            code_to_id, name_to_id, ambiguous_names,
            q3_data, q3_cleaned, t3_data, t3_std, t3_alias,
            cross_province_map
        )
        # 写回 all_results
        for idx, fill_info in new_fills.items():
            if idx not in all_results:
                all_results[idx] = fill_info

        hit_count = len(new_fills)
        still_empty = sum(1 for i in range(total_rows) if i not in all_results)
        cumulative_hit = len(all_results)
        pct = cumulative_hit / total_rows * 100

        # 收集样本
        hit_samples = []
        for idx in list(new_fills.keys())[:5]:
            row = q2_rows[idx]
            info = new_fills[idx]
            hit_samples.append(f"{row['material_name_raw'][:50]} → {info['material_id']}")

        miss_samples = []
        empty_idxs = [i for i in range(total_rows) if i not in all_results]
        for idx in empty_idxs[:5]:
            row = q2_rows[idx]
            miss_samples.append(f"{row['material_name_raw'][:50]} [{row.get('province','')}]")

        print_stage_report(
            f"Q2-{stage_name} (conf={confidence})",
            hit_count, hit_count + len(empty_idxs),
            hit_samples, miss_samples
        )
        print(f"  累计覆盖率: {cumulative_hit}/{total_rows} ({pct:.1f}%)")

        # 阶段性自动保存
        if not DRY_RUN and hit_count > 0:
            _autosave(q2_rows, q2_fields, all_results)

    # ── 5. AI 阶段（如果还有剩余） ──
    remaining = [i for i in range(total_rows) if i not in all_results]
    if remaining:
        print(f"\n[4/8] DeepSeek AI 推断 — {len(remaining)} 行待处理")
        if SKIP_AI:
            print(f"  [SKIP] 跳过 AI 阶段，{len(remaining)} 行保持未填充")
            if remaining:
                print("  待 AI 样本:")
                for idx in remaining[:10]:
                    r = q2_rows[idx]
                    cleaned, _ = clean_material_name(r['material_name_raw'])
                    print(f"    - {r['material_name_raw'][:60]} | 清洗后: {cleaned[:40]} | {r.get('province','')}")
        else:
            ai_fill = stage8_deepseek(q2_rows, remaining, all_results, t3_data)
            for idx, fill_info in ai_fill.items():
                all_results[idx] = fill_info
            if ai_fill:
                _autosave(q2_rows, q2_fields, all_results)
    else:
        print(f"\n[4/8] DeepSeek AI 推断 — 无需运行，全部已填充")

    # ── 6. 最终统计 ──
    final_filled = len(all_results)
    final_pct = final_filled / total_rows * 100
    print(f"\n{'='*60}")
    print(f"最终结果: {final_filled}/{total_rows} ({final_pct:.1f}%)")
    print(f"初始: {filled_before} → 新增: {final_filled - cumulative_start}")

    # 按阶段统计
    method_counts = defaultdict(int)
    for info in all_results.values():
        method_counts[info.get('match_method', 'unknown')] += 1
    print("\n按匹配方法分布:")
    for method, count in sorted(method_counts.items(), key=lambda x: -x[1]):
        print(f"  {method}: {count} ({count/total_rows*100:.1f}%)")

    # ── 7. 写入 ──
    if not DRY_RUN:
        print("\n[7/8] 写入结果...")
        _apply_and_write(q2_rows, q2_fields, all_results, Q2_PATH, Q2_INDEX_PATH)

        # 更新进度文件（标记完成）
        # 将有 all_results 的 converted to str keys
        save_progress({str(k): v for k, v in all_results.items()})

        print("  写入完成!")
    else:
        print("\n[DRY RUN] 跳过写入。使用以下命令实际运行:")
        print("  python3 工具脚本/补充Q2_material_id.py")

    print("\n完成!")


# ── 阶段 1: 内部 code→id ───────────────────────────────────

def stage1_internal_code(q2_rows, all_results, confidence,
                         code_to_id, name_to_id, ambiguous_names,
                         q3_data, q3_cleaned, t3_data, t3_std, t3_alias,
                         cross_province_map):
    """相同 material_code 且该 code 已唯一映射到 material_id，则填充"""
    new_fills = {}
    for i, row in enumerate(q2_rows):
        if i in all_results:
            continue
        code = row.get('material_code', '').strip()
        if code in code_to_id:
            mid = code_to_id[code]
            cleaned, _ = clean_material_name(row['material_name_raw'])
            new_fills[i] = {
                'material_id': mid,
                'match_method': 'internal_code_to_id',
                'match_confidence': confidence,
                'match_source': f'material_code:{code}',
                'match_cleaned_name': cleaned,
            }
    return new_fills


# ── 阶段 2: 内部 name→id ───────────────────────────────────

def stage2_internal_name(q2_rows, all_results, confidence,
                         code_to_id, name_to_id, ambiguous_names,
                         q3_data, q3_cleaned, t3_data, t3_std, t3_alias,
                         cross_province_map):
    """相同 material_name_raw 已唯一映射到 material_id，则填充"""
    new_fills = {}
    for i, row in enumerate(q2_rows):
        if i in all_results:
            continue
        name = row['material_name_raw'].strip()
        if name in name_to_id:
            mid = name_to_id[name]
            cleaned, _ = clean_material_name(name)
            new_fills[i] = {
                'material_id': mid,
                'match_method': 'internal_name_to_id',
                'match_confidence': confidence,
                'match_source': f'internal_name:{name[:40]}',
                'match_cleaned_name': cleaned,
            }
        elif name in ambiguous_names:
            pass  # 歧义不填
    return new_fills


# ── 阶段 3: Q3 精确匹配 ────────────────────────────────────

def stage3_q3_exact(q2_rows, all_results, confidence,
                    code_to_id, name_to_id, ambiguous_names,
                    q3_data, q3_cleaned, t3_data, t3_std, t3_alias,
                    cross_province_map):
    """Q2.material_name_raw 精确等于 Q3.material_name_raw"""
    new_fills = {}
    for i, row in enumerate(q2_rows):
        if i in all_results:
            continue
        name = row['material_name_raw'].strip()
        if name and name in q3_data:
            q3_info = q3_data[name]
            mid = q3_info['material_id']
            cleaned, _ = clean_material_name(name)
            new_fills[i] = {
                'material_id': mid,
                'match_method': 'q3_exact_match',
                'match_confidence': confidence,
                'match_source': f'Q3:{q3_info["mapping_id"]}',
                'match_cleaned_name': cleaned,
            }
    return new_fills


# ── 阶段 4: 清洗后 Q3 匹配 ─────────────────────────────────

def stage4_q3_cleaned(q2_rows, all_results, confidence,
                      code_to_id, name_to_id, ambiguous_names,
                      q3_data, q3_cleaned, t3_data, t3_std, t3_alias,
                      cross_province_map):
    """双方清洗后精确匹配"""
    new_fills = {}
    for i, row in enumerate(q2_rows):
        if i in all_results:
            continue
        name = row['material_name_raw'].strip()
        if not name:
            continue
        cleaned, quality = clean_material_name(name)
        if quality < 0.5 or not cleaned or len(cleaned) < 3:
            continue
        key = cleaned.lower()
        if key in q3_cleaned:
            mid = q3_cleaned[key]
            new_fills[i] = {
                'material_id': mid,
                'match_method': 'q3_cleaned_match',
                'match_confidence': confidence,
                'match_source': f'Q3-cleaned:{cleaned[:40]}',
                'match_cleaned_name': cleaned,
            }
    return new_fills


# ── 阶段 5: T3 别名精确匹配 ────────────────────────────────

def stage5_t3_alias(q2_rows, all_results, confidence,
                    code_to_id, name_to_id, ambiguous_names,
                    q3_data, q3_cleaned, t3_data, t3_std, t3_alias,
                    cross_province_map):
    """清洗后名称精确匹配 T3 别名或标准名"""
    new_fills = {}
    for i, row in enumerate(q2_rows):
        if i in all_results:
            continue
        name = row['material_name_raw'].strip()
        if not name:
            continue
        cleaned, quality = clean_material_name(name)
        if quality < 0.4 or not cleaned or len(cleaned) < 2:
            continue
        key = cleaned.lower()

        # 先查别名（更精确的匹配）
        alias_ids = t3_alias.get(key, set())
        std_id = t3_std.get(key)

        if len(alias_ids) == 1:
            mid = list(alias_ids)[0]
        elif std_id:
            mid = std_id
        elif len(alias_ids) > 1:
            continue  # 歧义，跳过
        else:
            continue

        t3_info = t3_data.get(mid, {})
        new_fills[i] = {
            'material_id': mid,
            'match_method': 't3_alias_exact',
            'match_confidence': confidence,
            'match_source': f'T3:{t3_info.get("standard_name", mid)[:40]}',
            'match_cleaned_name': cleaned,
        }
    return new_fills


# ── 阶段 6: T3 关键词+子串匹配 ─────────────────────────────

def stage6_t3_keyword(q2_rows, all_results, confidence,
                      code_to_id, name_to_id, ambiguous_names,
                      q3_data, q3_cleaned, t3_data, t3_std, t3_alias,
                      cross_province_map):
    """关键词命中 + 排除检查"""
    new_fills = {}
    for i, row in enumerate(q2_rows):
        if i in all_results:
            continue
        name = row['material_name_raw'].strip()
        if not name:
            continue
        cleaned, quality = clean_material_name(name)
        if quality < 0.3 or not cleaned:
            continue

        mid = keyword_substring_match(cleaned, t3_data)
        if mid:
            t3_info = t3_data.get(mid, {})
            new_fills[i] = {
                'material_id': mid,
                'match_method': 't3_keyword_substring',
                'match_confidence': confidence,
                'match_source': f'T3-kw:{t3_info.get("standard_name", mid)[:40]}',
                'match_cleaned_name': cleaned,
            }
    return new_fills


# ── 阶段 7: 跨省份转移 ─────────────────────────────────────

def stage7_cross_province(q2_rows, all_results, confidence,
                          code_to_id, name_to_id, ambiguous_names,
                          q3_data, q3_cleaned, t3_data, t3_std, t3_alias,
                          cross_province_map):
    """同名材料在其他省份已填 material_id，当前省份直接复用"""
    new_fills = {}
    for i, row in enumerate(q2_rows):
        if i in all_results:
            continue
        name = row['material_name_raw'].strip()
        if not name:
            continue
        cleaned, quality = clean_material_name(name)
        if quality < 0.4 or not cleaned or len(cleaned) < 3:
            continue
        key = cleaned.lower()
        province = row.get('province', '').strip()

        if key not in cross_province_map:
            continue

        # 取其他省份已经出现的 material_id
        prov_mids = cross_province_map[key]
        # 至少要在 2 个省份出现才转移
        candidates = [(mid, provs) for mid, provs in prov_mids.items()
                      if len(provs) >= 2 or province not in provs]
        if not candidates:
            candidates = [(mid, provs) for mid, provs in prov_mids.items()]

        if len(candidates) == 1:
            mid = candidates[0][0]
        elif len(candidates) > 1:
            # 有多个候选，选出现省份最多的
            candidates.sort(key=lambda x: -len(x[1]))
            if len(candidates[0][1]) > len(candidates[1][1]):
                mid = candidates[0][0]
            else:
                continue  # 无法确定，跳过
        else:
            continue

        new_fills[i] = {
            'material_id': mid,
            'match_method': 'cross_province_transfer',
            'match_confidence': confidence,
            'match_source': f'cross-prov:{",".join(sorted(prov_mids[mid]))}',
            'match_cleaned_name': cleaned,
        }
    return new_fills


# ── 阶段 8: DeepSeek AI 推断 ───────────────────────────────

def stage8_deepseek(q2_rows, remaining, all_results, t3_data):
    """批量送 AI 推断 material_id，参考 T3 标准库"""
    # 构建 T3 参考文本
    t3_ref = _build_t3_reference(t3_data)

    # 分批处理
    BATCH_SIZE = 20
    batches = [remaining[i:i+BATCH_SIZE] for i in range(0, len(remaining), BATCH_SIZE)]

    print(f"  共 {len(batches)} 批, 每批 {BATCH_SIZE} 行")

    new_fills = {}
    total_processed = 0

    for batch_idx, batch in enumerate(batches):
        if batch_idx % 10 == 0 and batch_idx > 0:
            print(f"    进度: {batch_idx}/{len(batches)} 批, 命中 {len(new_fills)}")

        batch_rows = [(i, q2_rows[i]) for i in batch]
        result = _call_deepseek_for_materials(batch_rows, t3_ref, t3_data)

        for idx, mid_info in result.items():
            if mid_info and mid_info.get('material_id'):
                new_fills[idx] = {
                    'material_id': mid_info['material_id'],
                    'match_method': 'deepseek_ai',
                    'match_confidence': 0.50,
                    'match_source': f"DS:{mid_info.get('reason', '')[:40]}",
                    'match_cleaned_name': mid_info.get('cleaned_name', ''),
                }

        total_processed += len(batch)

        # 每 5 批自动保存
        if not DRY_RUN and batch_idx % 5 == 4:
            merged = {**all_results, **new_fills}
            _autosave(q2_rows, None, merged)  # fields不需要，仅保存进度

    return new_fills


def _build_t3_reference(t3_data):
    """构建 T3 参考文本供 AI 使用"""
    lines = []
    for mid, info in sorted(t3_data.items()):
        aliases_str = ', '.join(info['aliases'][:4])
        lines.append(f"{mid}: {info['standard_name']} (别名: {aliases_str}) [{info['category']}]")
    return '\n'.join(lines)


def _call_deepseek_for_materials(batch_rows, t3_ref, t3_data):
    """调用 DeepSeek API 推断 material_id"""
    import os as _os
    import json as _json

    api_key = _os.environ.get('DEEPSEEK_API_KEY', '')
    if not api_key:
        # 尝试从 .env 读取
        env_path = BASE_DIR / '.env'
        if env_path.exists():
            with open(env_path) as f:
                for line in f:
                    line = line.strip()
                    if line.startswith('DEEPSEEK_API_KEY='):
                        api_key = line.split('=', 1)[1].strip()
                        _os.environ['DEEPSEEK_API_KEY'] = api_key
                        break
    if not api_key:
        print("  [WARN] 未找到 DEEPSEEK_API_KEY，跳过 AI 阶段")
        return {}

    # 构建 prompt
    items = []
    for idx, row in batch_rows:
        name = row.get('material_name_raw', '').strip()
        cleaned, _ = clean_material_name(name)
        quota_id = row.get('quota_id', '').strip()
        items.append(f"  [{idx}] 名称: {name} | 清洗后: {cleaned} | 定额: {quota_id}")

    items_text = '\n'.join(items)

    prompt = f"""你是建筑工程标准材料识别专家。请将以下定额材料映射到 T3 标准物料库。

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
            api_key=api_key,
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

        # 提取 JSON
        json_str = raw
        if '```json' in raw:
            json_str = raw.split('```json')[1].split('```')[0].strip()
        elif '```' in raw:
            json_str = raw.split('```')[1].split('```')[0].strip()

        results = _json.loads(json_str)
        # 验证在 T3 库中
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

def _autosave(q2_rows, q2_fields, all_results):
    """自动保存进度和 CSV"""
    # 更新 CSV 中的 material_id
    for i, row in enumerate(q2_rows):
        if i in all_results and not row.get('material_id', '').strip():
            row['material_id'] = all_results[i]['material_id']

    if q2_fields:
        write_csv(Q2_PATH, q2_rows, q2_fields)

    # 保存进度
    save_progress({str(k): v for k, v in all_results.items()})


def _apply_and_write(q2_rows, q2_fields, all_results, csv_path, index_path):
    """最终应用所有结果并写入 CSV 和索引文件"""
    # 更新 material_id
    for i, row in enumerate(q2_rows):
        if i in all_results:
            info = all_results[i]
            row['material_id'] = info['material_id']

    # 写 CSV
    write_csv(csv_path, q2_rows, q2_fields)

    # 重建 q2 索引
    print("  重建 Q2 索引...")
    index = defaultdict(list)
    for row in q2_rows:
        qid = row.get('quota_id', '').strip()
        if qid:
            index[qid].append(dict(row))

    import json as _json
    tmp_idx = str(index_path) + '.tmp'
    with open(tmp_idx, 'w', encoding='utf-8') as f:
        _json.dump(index, f, ensure_ascii=False)
    os.replace(tmp_idx, str(index_path))
    print(f"  Q2 索引已重建: {len(index)} 个定额ID, {index_path}")


if __name__ == '__main__':
    main()
