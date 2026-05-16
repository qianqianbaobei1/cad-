#!/usr/bin/env python3
"""
从 name_index、AI回填、人工补充、T3源数据 中提取别名，
只回填 category_aliases 和 material.aliases，没有数据源的就跳过。
"""
from __future__ import annotations

import csv
import json
import re
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CCE_JSON = ROOT / "标准知识库" / "三级分类材料库.json"
AI_CSV = ROOT / "知识库补丁流程" / "数据" / "三级分类材料别称AI回填.csv"
MANUAL_CSV = ROOT / "知识库补丁流程" / "数据" / "三级分类材料别称人工补充.csv"
T3_FJ_CSV = ROOT / "标准知识库" / "源数据" / "01_房屋建筑与装饰工程" / "CSV导出" / "03_t3_标准物料库.csv"
T3_AZ_CSV = ROOT / "标准知识库" / "源数据" / "02_通用安装工程" / "CSV导出" / "02_t3_标准物料库.csv"


def normalize(text: str) -> str:
    """归一化文本用于去重比较"""
    text = str(text or "").strip().lower()
    text = text.replace("（", "(").replace("）", ")")
    return re.sub(r"[\s　,，;；、。/\\]+", "", text)


def is_valid_alias(alias: str) -> bool:
    """过滤无效别名：空字符串、过长文本、纯符号"""
    a = str(alias or "").strip()
    if not a or len(a) < 1:
        return False
    # 归一化后为空串
    if not normalize(a):
        return False
    # 只有标点符号
    if re.fullmatch(r'[\s　,，;；、。/\\\-_=+*#@!$%^&()（）\[\]【】{}「」『』"\'´`¨~]+', a):
        return False
    # 太长的不是别名
    if len(a) > 60:
        return False
    return True


def parse_json_array(s: str) -> list[str]:
    if not s or s in ("[]", "{}", ""):
        return []
    try:
        val = json.loads(s)
        if isinstance(val, list):
            return [str(x).strip() for x in val if str(x).strip()]
        elif isinstance(val, str):
            return [x.strip() for x in re.split(r"[、,，;；]\s*", val) if x.strip()]
    except (json.JSONDecodeError, TypeError):
        return [x.strip() for x in re.split(r"[、,，;；]\s*", str(s)) if x.strip()]


def read_t3_aliases():
    """读取 T3 标准物料库，返回 {category_id: {aliases}} 和 {material_name_normalized: [aliases]}"""
    cat_aliases: dict[str, set[str]] = defaultdict(set)
    mat_aliases: dict[str, list[str]] = defaultdict(list)

    for csv_path in [T3_FJ_CSV, T3_AZ_CSV]:
        if not csv_path.exists():
            continue
        with csv_path.open(encoding="utf-8-sig", newline="") as f:
            for r in csv.DictReader(f):
                node_id = r.get("品类树节点ID", "").strip()
                name = r.get("标准名称", "").strip()
                aliases = parse_json_array(r.get("别名", ""))
                keywords = parse_json_array(r.get("特征关键词", ""))

                if node_id:
                    for a in aliases:
                        if normalize(a) != normalize(name):
                            cat_aliases[node_id].add(a)
                    for kw in keywords:
                        if normalize(kw) != normalize(name):
                            cat_aliases[node_id].add(kw)

                if name and aliases:
                    mat_aliases[normalize(name)] = aliases

    return dict(cat_aliases), dict(mat_aliases)


def read_supplement_csv(csv_path: Path):
    """读取人工补充/AI回填 CSV，返回 {category_id: [(alias, source, note)]}"""
    result: dict[str, list[dict]] = defaultdict(list)
    if not csv_path.exists():
        return dict(result)
    with csv_path.open(encoding="utf-8-sig", newline="") as f:
        for r in csv.DictReader(f):
            cat_id = r.get("末级分类ID", "").strip()
            alias = r.get("别称", "").strip()
            material_name = r.get("材料名称", "").strip()
            source = r.get("来源", "").strip()
            note = r.get("备注", "").strip()
            if cat_id and alias:
                result[cat_id].append({
                    "alias": alias,
                    "material_name": material_name,
                    "source": source,
                    "note": note,
                })
    return dict(result)


def main():
    print("=" * 60)
    print("加载 CCE 三级分类材料库...")
    with CCE_JSON.open(encoding="utf-8") as f:
        cce = json.load(f)

    categories = cce["categories"]
    name_index = cce.get("name_index", {})
    print(f"  分类数: {len(categories)}")
    print(f"  name_index 条目: {len(name_index)}")

    # 建立 category_id -> 分类对象 索引
    cat_by_id = {c["category_id"]: c for c in categories}

    # 1. 读取 T3 别名
    print("\n加载 T3 标准物料库别名...")
    t3_cat_aliases, t3_mat_aliases = read_t3_aliases()
    print(f"  T3 分类别名覆盖: {len(t3_cat_aliases)} 个节点")
    print(f"  T3 材料别名覆盖: {len(t3_mat_aliases)} 个材料")

    # 2. 读取 AI 回填
    print("\n加载 AI 回填别称...")
    ai_supp = read_supplement_csv(AI_CSV)
    print(f"  AI 回填覆盖: {len(ai_supp)} 个分类, {sum(len(v) for v in ai_supp.values())} 条")

    # 3. 读取人工补充
    print("\n加载人工补充别称...")
    manual_supp = read_supplement_csv(MANUAL_CSV)
    print(f"  人工补充覆盖: {len(manual_supp)} 个分类, {sum(len(v) for v in manual_supp.values())} 条")

    # 4. 从 name_index 反向提取每个 category 的别名
    print("\n从 name_index 提取别名...")
    nameindex_aliases: dict[str, set[str]] = defaultdict(set)
    for key, entries in name_index.items():
        for e in entries:
            cat_id = e.get("category_id", "")
            if not cat_id or cat_id not in cat_by_id:
                continue
            src = e.get("source", "")
            mat_name = e.get("material_name", "")
            input_name = e.get("input_name", "")
            cat_l3 = e.get("category_l3", "")

            # 如果 input_name 不等于 material_name 且不等于 分类L3名，就是别名
            if normalize(input_name) != normalize(mat_name) and normalize(input_name) != normalize(cat_l3):
                nameindex_aliases[cat_id].add(input_name)

    print(f"  name_index 别名覆盖: {len(nameindex_aliases)} 个分类")

    # === 开始回填 ===
    stats = {
        "cat_alias_filled": 0,
        "cat_alias_skipped": 0,
        "total_cat_aliases": 0,
        "mat_alias_filled": 0,
        "mat_alias_skipped": 0,
        "total_mat_aliases": 0,
    }

    for cat in categories:
        cat_id = cat["category_id"]
        cat_l3 = cat["category_l3"]
        cat_l3_norm = normalize(cat_l3)

        # === 收集分类级别别名 ===
        collected = set()

        # from T3
        for a in t3_cat_aliases.get(cat_id, set()):
            if is_valid_alias(a) and normalize(a) != cat_l3_norm:
                collected.add(a.strip())

        # from AI回填
        for item in ai_supp.get(cat_id, []):
            a = item["alias"]
            if is_valid_alias(a) and normalize(a) != cat_l3_norm and normalize(a) != normalize(item.get("material_name", "")):
                collected.add(a.strip())

        # from 人工补充
        for item in manual_supp.get(cat_id, []):
            a = item["alias"]
            if is_valid_alias(a) and normalize(a) != cat_l3_norm and normalize(a) != normalize(item.get("material_name", "")):
                collected.add(a.strip())

        # from name_index
        for a in nameindex_aliases.get(cat_id, set()):
            if is_valid_alias(a) and normalize(a) != cat_l3_norm:
                collected.add(a.strip())

        # 去重、过滤空串、排序
        final_aliases = sorted([a for a in collected if is_valid_alias(a)], key=lambda x: (len(x), x))

        if final_aliases:
            cat["category_aliases"] = final_aliases
            cat["alias_count"] = len(final_aliases)
            stats["cat_alias_filled"] += 1
            stats["total_cat_aliases"] += len(final_aliases)
        else:
            # 保持原样
            stats["cat_alias_skipped"] += 1

        # === 收集材料级别别名 ===
        for mat in cat.get("materials", []):
            mat_name = mat["material_name"]
            mat_name_norm = normalize(mat_name)
            mat_aliases = set()

            # from T3
            if mat_name_norm in t3_mat_aliases:
                for a in t3_mat_aliases[mat_name_norm]:
                    if is_valid_alias(a) and normalize(a) != mat_name_norm:
                        mat_aliases.add(a.strip())

            # from name_index (entries with different input_name)
            if mat_name_norm in name_index:
                for e in name_index[mat_name_norm]:
                    inp = e.get("input_name", "")
                    if is_valid_alias(inp) and normalize(inp) != mat_name_norm:
                        mat_aliases.add(inp.strip())

            final_mat_aliases = sorted([a for a in mat_aliases if is_valid_alias(a)], key=lambda x: (len(x), x))
            if final_mat_aliases:
                mat["aliases"] = final_mat_aliases
                stats["mat_alias_filled"] += 1
                stats["total_mat_aliases"] += len(final_mat_aliases)
            else:
                stats["mat_alias_skipped"] += 1

    # === 全局清理：移除所有空字符串别名（包括之前运行的残留） ===
    cleaned_cat = 0
    cleaned_mat = 0
    for cat in categories:
        old_len = len(cat.get("category_aliases", []))
        cat["category_aliases"] = [a for a in cat.get("category_aliases", []) if is_valid_alias(a)]
        if len(cat["category_aliases"]) != old_len:
            cleaned_cat += 1
        cat["alias_count"] = len(cat["category_aliases"])
        for mat in cat.get("materials", []):
            old_mlen = len(mat.get("aliases", []))
            mat["aliases"] = [a for a in mat.get("aliases", []) if is_valid_alias(a)]
            if len(mat["aliases"]) != old_mlen:
                cleaned_mat += 1

    # 重新统计
    total_cat_aliases = sum(len(c.get("category_aliases", [])) for c in categories)
    total_filled = sum(1 for c in categories if c.get("category_aliases"))
    if cleaned_cat or cleaned_mat:
        print(f"\n清理残留: {cleaned_cat} 个分类、{cleaned_mat} 个材料的空别名已移除")

    # 更新摘要
    cce["summary"]["alias_count"] = total_cat_aliases
    cce["summary"]["category_with_alias_count"] = total_filled
    cce["summary"]["category_alias_backfilled_at"] = __import__("datetime").datetime.now().isoformat(timespec="seconds")

    # 保存
    print("\n" + "=" * 60)
    print("回填结果:")
    print(f"  分类别称 - 已补充: {total_filled}, 跳过(无数据): {len(categories) - total_filled}, 总别名数: {total_cat_aliases}")
    print(f"  材料别名 - 已补充: {stats['mat_alias_filled']}, 跳过(无数据): {stats['mat_alias_skipped']}, 总别名数: {stats['total_mat_aliases']}")

    with CCE_JSON.open("w", encoding="utf-8") as f:
        json.dump(cce, f, ensure_ascii=False, indent=2)
    print(f"\n已保存: {CCE_JSON}")

    # 按一级分类统计补充情况
    print("\n" + "=" * 60)
    print("按一级分类统计补充情况:")
    l1_stats = defaultdict(lambda: {"total": 0, "filled": 0, "aliases": 0})
    for cat in categories:
        l1 = cat["category_l1"]
        l1_stats[l1]["total"] += 1
        ac = len(cat.get("category_aliases", []))
        if ac > 0:
            l1_stats[l1]["filled"] += 1
            l1_stats[l1]["aliases"] += ac

    for l1, s in sorted(l1_stats.items(), key=lambda x: -x[1]["total"]):
        pct = s["filled"] / s["total"] * 100 if s["total"] else 0
        print(f"  {l1}: {s['filled']}/{s['total']} ({pct:.0f}%) 别名{s['aliases']}条")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())