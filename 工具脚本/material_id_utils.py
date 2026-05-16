"""
Q2/T2 material_id 补充共享工具模块。
提供名称清洗、T3索引构建、多阶段匹配等功能。
"""
import csv
import json
import re
import os
import sys
from collections import defaultdict
from pathlib import Path
from typing import Optional

BASE_DIR = Path(__file__).resolve().parent.parent
Q2_PATH = BASE_DIR / "标准知识库/源数据/01_定额库/Q2_定额材料消耗.csv"
T2_PATH = BASE_DIR / "标准知识库/源数据/01_定额库/T2_清单材料映射库.csv"
Q3_PATH = BASE_DIR / "标准知识库/源数据/01_定额库/Q3_定额材料映射.csv"
T3_AZ_PATH = BASE_DIR / "标准知识库/T3_安装_标准物料库.csv"
T3_FJ_PATH = BASE_DIR / "标准知识库/T3_房建_标准物料库.csv"
Q2_INDEX_PATH = BASE_DIR / "项目数据/本地知识库包/索引/q2_按定额ID.json"
PROGRESS_PATH = BASE_DIR / "过程数据/补充material_id_progress.json"


# ── 名称清洗 ───────────────────────────────────────────────

# 数量/单位混杂模式：数字后面跟单位
QUANTITY_PATTERNS = [
    re.compile(r'\s+(kg|m|m²|m2|m³|m3|套|个|块|根|支|台|只|t|千块|百个|十套|%)\s*[\d.,]*$', re.IGNORECASE),
    re.compile(r'\s+[\d.]+$'),
]

# 尾部括号内容（成品/综合/含轨道等）
BRACKET_SUFFIX = re.compile(r'[（(][^)）]*[)）]$')

# 省份/标记词
PROVINCE_MARKERS = re.compile(r'[\s（(]*(京|省|市|区|通|装|安|给|排|消|电|暖|智|风|水|气|油)[\s）)]*$')

# 规格混杂：尾部带数字+单位且无实际材料意义的
SPEC_TAIL = re.compile(r'\s+\d+[安A](?:以内|以上|及以下)?$')

# 全角转半角
FULLWIDTH_MAP = str.maketrans(
    '０１２３４５６７８９ＡＢＣＤＥＦＧＨＩＪＫＬＭＮＯＰＱＲＳＴＵＶＷＸＹＺａｂｃｄｅｆｇｈｉｊｋｌｍｎｏｐｑｒｓｔｕｖｗｘｙｚ（）．',
    '0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz().'
)

# GB/T、JC、ISO等标准编号
STANDARD_CODE = re.compile(r'\s*(?:GB[/T]*|JC[/T]*|ISO|ASTM|DIN|JIS|BS|EN)[\s/\d\-:.]*$', re.IGNORECASE)

# 以 "kg"、"m"等开头的单位噪声（如 "kg 12"、"m 0.5"）
LEADING_UNIT = re.compile(r'^(?:kg|m|m²|m2|m³|m3|套|个|块|根|支|台|只|t|千块|百个|十套)\s+[\d.]+', re.IGNORECASE)

# 尾部的 / 含 等
TAIL_MISC = re.compile(r'[\s]+[/／][\s]*$')

# 明显的乱码标记 — 这些词作为独立 token 出现时表示乱码
GARBLED_MARKERS = re.compile(
    r'[（(]\s*元\s*[）)]'         # (元) 乱码标记
    r'|\s(?:省|通|装|额)\s'      # 独立噪声字
    r'|\s(?:制|及|采)\s'        # 独立噪声字
    r'|程消'                     # "给程消" 模式中的一部分
    r'|\s\d+\s*(?:省|通|装|工)\s',  # 20省、20通、24工 等
    re.IGNORECASE
)


def clean_material_name(raw: str) -> tuple:
    """
    清洗材料名称，返回 (cleaned, quality)。
    quality 是剩余长度/原始长度的比例，<0.3 视为不可用。
    """
    if not raw or not raw.strip():
        return '', 0.0

    original = raw.strip()
    result = original

    # 1. 全角转半角
    result = result.translate(FULLWIDTH_MAP)

    # 2. 去掉首尾 # 号
    result = result.strip('#').strip()

    # 3. 去掉尾部括号内容：(成品)、(综合)、(含轨道)等
    result = BRACKET_SUFFIX.sub('', result).strip()

    # 4. 尾部 - 号
    result = result.rstrip('-').strip()

    # 5. 去掉规格混杂：20安、通、装 等尾部标记
    result = SPEC_TAIL.sub('', result).strip()

    # 6. 去掉标准编号
    result = STANDARD_CODE.sub('', result).strip()

    # 7. 去掉尾部 / 号
    result = TAIL_MISC.sub('', result).strip()

    # 8. 合并空格
    result = re.sub(r'\s+', ' ', result).strip()

    # 9. 再次去尾部 - 和 #号
    result = result.rstrip('-').strip('#').strip()

    if not result:
        return '', 0.0

    # 10. 检查乱码标记
    has_garbled = bool(GARBLED_MARKERS.search(result))

    quality = len(result) / max(len(original), 1)
    if has_garbled:
        quality = min(quality, 0.45)  # 降低含乱码标记名称的质量分数
    return result, min(quality, 1.0)


def is_likely_garbled(name: str) -> bool:
    """检查名称是否可能包含乱码标记"""
    return bool(GARBLED_MARKERS.search(name))


# ── T3 索引构建 ─────────────────────────────────────────────

def load_t3_library():
    """加载 T3 标准物料库，返回 {material_id: {aliases, standard_name, category}}"""
    t3_data = {}
    for path in [T3_AZ_PATH, T3_FJ_PATH]:
        if not path.exists():
            continue
        with open(path, encoding='utf-8-sig') as f:
            for row in csv.DictReader(f):
                mid = row.get('物料ID', '').strip()
                if not mid:
                    continue
                std_name = row.get('标准名称', '').strip()
                category = row.get('分类(一级)', '').strip()
                # 解析别名字段
                aliases_raw = row.get('别名', '[]').strip()
                try:
                    aliases = json.loads(aliases_raw) if aliases_raw else []
                except json.JSONDecodeError:
                    aliases = [a.strip() for a in aliases_raw.strip('[]').split(',') if a.strip()]

                keywords_raw = row.get('特征关键词', '[]').strip()
                try:
                    keywords = json.loads(keywords_raw) if keywords_raw else []
                except json.JSONDecodeError:
                    keywords = [k.strip() for k in keywords_raw.strip('[]').split(',') if k.strip()]

                exclude_raw = row.get('排除关键词', '[]').strip()
                try:
                    exclude_kw = json.loads(exclude_raw) if exclude_raw else []
                except json.JSONDecodeError:
                    exclude_kw = [e.strip() for e in exclude_raw.strip('[]').split(',') if e.strip()]

                t3_data[mid] = {
                    'standard_name': std_name,
                    'aliases': aliases,
                    'keywords': keywords,
                    'exclude_keywords': exclude_kw,
                    'category': category,
                }
    return t3_data


def build_t3_name_index(t3_data):
    """
    构建 T3 名称→ID 索引。
    返回两个 dict: {cleaned_name: material_id} 和 {alias: material_id}
    """
    standard_to_id = {}  # 标准名称 → ID
    alias_to_id = defaultdict(set)  # 别名 → ID 集合

    for mid, info in t3_data.items():
        # 标准名称
        std_clean, _ = clean_material_name(info['standard_name'])
        if std_clean:
            standard_to_id[std_clean.lower()] = mid

        # 别名
        for alias in info['aliases']:
            alias_clean, _ = clean_material_name(alias)
            if alias_clean:
                alias_to_id[alias_clean.lower()].add(mid)

    return standard_to_id, alias_to_id


# ── Q3 索引 ──────────────────────────────────────────────────

def load_q3_mapping():
    """加载 Q3 定额材料映射，返回 {material_name_raw: {material_id, matched_name, ...}}"""
    q3 = {}
    if not Q3_PATH.exists():
        print("[WARN] Q3 file not found:", Q3_PATH)
        return q3
    with open(Q3_PATH, encoding='utf-8-sig') as f:
        for row in csv.DictReader(f):
            raw_name = row.get('material_name_raw', '').strip()
            mid = row.get('material_id', '').strip()
            if raw_name and mid:
                q3[raw_name] = {
                    'mapping_id': row.get('mapping_id', '').strip(),
                    'material_id': mid,
                    'matched_name': row.get('matched_name', '').strip(),
                    'category': row.get('category', '').strip(),
                    'match_confidence': row.get('match_confidence', '').strip(),
                }
    return q3


def build_q3_cleaned_index(q3_data):
    """构建清洗后的 Q3 索引 {cleaned_name: material_id}"""
    idx = {}
    for raw_name, info in q3_data.items():
        cleaned, quality = clean_material_name(raw_name)
        # Q3 侧也需要质量检查和长度检查
        if cleaned and quality >= 0.5 and len(cleaned) >= 3:
            key = cleaned.lower()
            if key not in idx:
                idx[key] = info['material_id']
    return idx


# ── 内部映射 ─────────────────────────────────────────────────

def build_internal_code_index(rows, id_col='material_id', code_col='material_code',
                              min_support=5):
    """
    构建 material_code → material_id 内部映射。
    只保留一致的映射（同一个 code 只映射到唯一一个 id）。
    min_support: 至少需要多少行确认此映射。
    """
    code_to_ids = defaultdict(set)
    code_row_count = defaultdict(int)
    for row in rows:
        mid = row.get(id_col, '').strip()
        code = row.get(code_col, '').strip()
        if mid and code:
            code_to_ids[code].add(mid)
            code_row_count[code] += 1

    # 只保留一对一且有足够支持的
    return {code: list(ids)[0]
            for code, ids in code_to_ids.items()
            if len(ids) == 1 and code_row_count[code] >= min_support}


def build_internal_name_index(rows, id_col='material_id', name_col='material_name_raw',
                              min_name_length=3):
    """
    构建 material_name_raw → material_id 内部映射。
    排除歧义名称（同一个 name 对应多个不同 id）和过短名称。
    """
    name_to_ids = defaultdict(set)
    for row in rows:
        mid = row.get(id_col, '').strip()
        name = row.get(name_col, '').strip()
        if mid and name and len(name) >= min_name_length:
            name_to_ids[name].add(mid)

    # 排除歧义
    ambiguous = {name for name, ids in name_to_ids.items() if len(ids) > 1}
    result = {}
    for name, ids in name_to_ids.items():
        if len(ids) == 1:
            result[name] = list(ids)[0]

    return result, ambiguous


# ── 关键词+子串匹配 ──────────────────────────────────────────

def keyword_substring_match(name: str, t3_data: dict) -> Optional[str]:
    """
    对清洗后的名称做关键词+子串匹配。
    需要至少 2 个命中（keyword+alias+substring组合）或 1 个别名命中。
    返回匹配到的 material_id 或 None。
    """
    if len(name) < 2:
        return None

    name_lower = name.lower()

    # 收集候选
    candidates = []
    for mid, info in t3_data.items():
        std_lower = info['standard_name'].lower()
        # 检查排除关键词
        excluded = False
        for ek in info['exclude_keywords']:
            if ek.lower() in name_lower:
                excluded = True
                break
        if excluded:
            continue

        # 关键词命中
        keyword_hits = []
        for kw in info['keywords']:
            if kw.lower() in name_lower:
                keyword_hits.append(kw)
        # 别名命中（包含子串命中）
        alias_hits = []
        for alias in info['aliases']:
            if alias.lower() in name_lower:
                alias_hits.append(alias)
        # 标准名子串命中（双向）
        sub_hit = std_lower in name_lower or name_lower in std_lower

        total_hits = len(keyword_hits) + len(alias_hits) + (1 if sub_hit else 0)

        # 至少需要 2 个总命中，或 1 个别名命中
        if total_hits >= 2 or len(alias_hits) >= 1:
            score = len(keyword_hits) * 2 + len(alias_hits) * 3 + (1 if sub_hit else 0)
            candidates.append((mid, score))

    if not candidates:
        return None

    # 取最高分
    candidates.sort(key=lambda x: -x[1])
    best_mid, best_score = candidates[0]

    # 如果有同分的，不选（歧义）
    if sum(1 for _, s in candidates if s == best_score) > 1:
        return None

    return best_mid


# ── 进度管理 ─────────────────────────────────────────────────

def load_progress():
    """加载进度文件 {quota_id/row_key: filled_data}"""
    if PROGRESS_PATH.exists():
        with open(PROGRESS_PATH, encoding='utf-8') as f:
            return json.load(f)
    return {}


def save_progress(progress: dict):
    """保存进度到文件"""
    PROGRESS_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = str(PROGRESS_PATH) + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(progress, f, ensure_ascii=False, indent=2)
    os.replace(tmp, str(PROGRESS_PATH))


# ── CSV 读写 ─────────────────────────────────────────────────

def read_csv(path):
    """读取 CSV，返回 (rows_list, fieldnames)"""
    with open(path, encoding='utf-8-sig') as f:
        reader = csv.DictReader(f)
        rows = list(reader)
        return rows, reader.fieldnames


def write_csv(path, rows, fieldnames):
    """写入 CSV"""
    tmp = str(path) + '.tmp'
    with open(tmp, 'w', encoding='utf-8-sig', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    os.replace(tmp, str(path))


# ── 交叉验证 ─────────────────────────────────────────────────

def cross_validate(q2_rows, t2_rows):
    """
    交叉验证 Q2 和 T2 的 material_id 一致性。
    通过 quota_id + material_name_raw 关联两个表，检查 material_id 是否一致。
    """
    # 构建 T2 查找：{(quota_id, material_name_raw): material_id}
    t2_map = {}
    for row in t2_rows:
        key = (row.get('quota_id', '').strip(), row.get('material_name_raw', '').strip())
        mid = row.get('material_id', '').strip()
        if mid:
            t2_map[key] = mid

    conflicts = []
    for row in q2_rows:
        key = (row.get('quota_id', '').strip(), row.get('material_name_raw', '').strip())
        q2_mid = row.get('material_id', '').strip()
        t2_mid = t2_map.get(key, '')
        if q2_mid and t2_mid and q2_mid != t2_mid:
            conflicts.append({
                'quota_id': key[0],
                'material_name': key[1],
                'q2_material_id': q2_mid,
                't2_material_id': t2_mid,
            })

    return conflicts


# ── 统计工具 ─────────────────────────────────────────────────

def print_stage_report(stage_name: str, hit_count: int, total: int,
                       samples_hit: list, samples_miss: list):
    """打印每阶段统计报告"""
    pct = hit_count / total * 100 if total > 0 else 0
    print(f"\n{'='*60}")
    print(f"[{stage_name}] 命中: {hit_count}/{total} ({pct:.1f}%)")
    if samples_hit:
        print(f"  命中样本:")
        for s in samples_hit[:5]:
            print(f"    + {s}")
    if samples_miss:
        print(f"  未命中样本:")
        for s in samples_miss[:5]:
            print(f"    - {s}")
