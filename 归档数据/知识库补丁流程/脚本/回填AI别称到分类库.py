#!/usr/bin/env python3
"""将 AI三级分类别称候选.jsonl 中的别称合并到 三级分类材料库.json。

合并规则：
- 按 category_id 匹配分类
- 新增别称不在已有别称中才加入
- 按 source=ai_candidate 标记来源
- 自动重建 name_index 并更新统计
"""
from __future__ import annotations

import json
import re
import time
from collections import defaultdict
from copy import deepcopy
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
候选JSONL = ROOT / "知识库补丁流程" / "数据" / "AI三级分类别称候选.jsonl"
分类库路径 = ROOT / "标准知识库" / "三级分类材料库.json"
备份目录 = ROOT / "过程数据" / "分类库备份"


def 归一(text: str) -> str:
    text = str(text or "").strip().lower()
    text = text.replace("（", "(").replace("）", ")")
    return re.sub(r"[\s　,，;；、。/\\]+", "", text)


def 读JSONL(path: Path):
    if not path.exists():
        return []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            yield json.loads(line)


def 加载候选(path: Path):
    """加载 AI 候选数据，去重，只保留有别名和 materials 的。"""
    tasks: dict[str, dict] = {}  # task_id → best task
    for task in 读JSONL(path):
        if task.get("status") != "ok":
            continue
        tid = task.get("task_id", "")
        if tid not in tasks:
            tasks[tid] = task
        else:
            # Keep the one with more aliases
            old_count = 0
            old_r = tasks[tid].get("result", {})
            for a in old_r.get("category_aliases", []) or []:
                old_count += 1
            for m in old_r.get("materials", []) or []:
                old_count += len(m.get("aliases", []) or [])
            new_count = 0
            new_r = task.get("result", {})
            for a in new_r.get("category_aliases", []) or []:
                new_count += 1
            for m in new_r.get("materials", []) or []:
                new_count += len(m.get("aliases", []) or [])
            if new_count > old_count:
                tasks[tid] = task
    return list(tasks.values())


def 合并(candidates: list[dict], kb_path: Path) -> dict:
    kb = json.loads(kb_path.read_text(encoding="utf-8"))

    # 建立 category_id → category 索引
    cat_by_id: dict[str, dict] = {}
    for c in kb["categories"]:
        cid = str(c.get("category_id", ""))
        if cid:
            cat_by_id[cid] = c

    # 建立已有别称集合 (per category)
    existing_cat_aliases: dict[str, set[str]] = defaultdict(set)
    existing_mat_aliases: dict[str, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
    for cid, cat in cat_by_id.items():
        for a in cat.get("category_aliases", []) or []:
            existing_cat_aliases[cid].add(归一(a.get("alias", "")))
        for m in cat.get("materials", []) or []:
            mname = 归一(m.get("material_name", ""))
            for a in m.get("aliases", []) or []:
                existing_mat_aliases[cid][mname].add(归一(a.get("alias", "")))

    stats = {
        "new_cat_aliases": 0,
        "new_mat_aliases": 0,
        "skipped_dup": 0,
        "skipped_no_match": 0,
        "categories_updated": set(),
    }

    for task in candidates:
        data = task.get("result") or {}
        category_id = str(data.get("category_id", "")).strip()
        if not category_id:
            continue

        cat = cat_by_id.get(category_id)
        if not cat:
            stats["skipped_no_match"] += 1
            continue

        # ── 合并分类别名 ──
        for row in data.get("category_aliases", []) or []:
            alias = str(row.get("alias", "")).strip()
            if not alias:
                continue
            norm = 归一(alias)
            if norm in existing_cat_aliases[category_id]:
                stats["skipped_dup"] += 1
                continue
            existing_cat_aliases[category_id].add(norm)
            cat.setdefault("category_aliases", []).append({
                "alias": alias,
                "source": "ai_candidate",
                "note": f"{row.get('confidence', 'medium')}: {row.get('reason', '')}",
            })
            stats["new_cat_aliases"] += 1
            stats["categories_updated"].add(category_id)

        # ── 合并材料别名 ──
        for material in data.get("materials", []) or []:
            material_name = str(material.get("material_name", "")).strip()
            if not material_name:
                continue
            norm_mname = 归一(material_name)

            # 在分类的材料列表中查找匹配材料
            matched = None
            for m in cat.get("materials", []) or []:
                if 归一(m.get("material_name", "")) == norm_mname:
                    matched = m
                    break
            if not matched:
                # 尝试包含匹配
                for m in cat.get("materials", []) or []:
                    db_name = 归一(m.get("material_name", ""))
                    if db_name and (norm_mname in db_name or db_name in norm_mname):
                        matched = m
                        break
            if not matched:
                stats["skipped_no_match"] += 1
                continue

            for row in material.get("aliases", []) or []:
                alias = str(row.get("alias", "")).strip()
                if not alias:
                    continue
                norm = 归一(alias)
                if norm in existing_mat_aliases[category_id][norm_mname]:
                    stats["skipped_dup"] += 1
                    continue
                existing_mat_aliases[category_id][norm_mname].add(norm)
                matched.setdefault("aliases", []).append({
                    "alias": alias,
                    "source": "ai_candidate",
                    "note": f"{row.get('confidence', 'medium')}: {row.get('reason', '')}",
                })
                stats["new_mat_aliases"] += 1
                stats["categories_updated"].add(category_id)

    # ── 更新各分类的 alias_count ──
    for cid in stats["categories_updated"]:
        cat = cat_by_id[cid]
        total = len(cat.get("category_aliases", []) or [])
        for m in cat.get("materials", []) or []:
            total += len(m.get("aliases", []) or [])
        cat["alias_count"] = total

    # ── 重建 name_index ──
    ni = {}
    for c in kb["categories"]:
        cid = c.get("category_id", "")
        path = c.get("category_path", "")
        l3 = c.get("category_l3", "")
        for a in c.get("category_aliases", []) or []:
            key = 归一(a.get("alias", ""))
            if not key:
                continue
            ni.setdefault(key, []).append({
                "material_name": l3 or "",
                "category_id": cid,
                "category_l3": l3 or "",
                "category_path": path or "",
                "source": a.get("source", ""),
            })
        for m in c.get("materials", []) or []:
            mname = m.get("material_name", "")
            ni.setdefault(归一(mname), []).append({
                "material_name": mname,
                "category_id": cid,
                "category_l3": l3 or "",
                "category_path": path or "",
                "source": m.get("source", "cce"),
            })
            for a in m.get("aliases", []) or []:
                key = 归一(a.get("alias", ""))
                if not key:
                    continue
                ni.setdefault(key, []).append({
                    "material_name": mname,
                    "category_id": cid,
                    "category_l3": l3 or "",
                    "category_path": path or "",
                    "source": a.get("source", ""),
                })
    kb["name_index"] = ni

    # ── 更新 summary ──
    alias_total = 0
    for c in kb["categories"]:
        alias_total += len(c.get("category_aliases", []) or [])
        for m in c.get("materials", []) or []:
            alias_total += len(m.get("aliases", []) or [])
    kb["summary"]["alias_count"] = alias_total
    kb["summary"]["name_index_count"] = len(ni)
    kb["summary"]["ai_alias_file"] = str(候选JSONL)
    kb["version"] = kb.get("version", "1.0.0").split("+")[0] + f"+ai_backfill_{time.strftime('%Y%m%d_%H%M%S')}"

    return kb, stats


def main() -> int:
    print("=" * 60)
    print("AI 别称候选 → 三级分类材料库 合并")
    print("=" * 60)

    candidates = 加载候选(候选JSONL)
    print(f"\n加载 AI 候选: {len(candidates)} 个有效任务")

    kb, stats = 合并(candidates, 分类库路径)

    print(f"\n合并结果:")
    print(f"  新增分类别名: {stats['new_cat_aliases']}")
    print(f"  新增材料别名: {stats['new_mat_aliases']}")
    print(f"  跳过(重复):   {stats['skipped_dup']}")
    print(f"  跳过(无匹配): {stats['skipped_no_match']}")
    print(f"  更新分类数:   {len(stats['categories_updated'])}")

    if stats["new_cat_aliases"] == 0 and stats["new_mat_aliases"] == 0:
        print("\n无新别称需要合并，跳过写入。")
        return 0

    # 备份
    备份目录.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y%m%d_%H%M%S")
    backup_path = 备份目录 / f"三级分类材料库_backup_{ts}.json"
    backup_path.write_text(分类库路径.read_text(encoding="utf-8"), encoding="utf-8")
    print(f"\n备份: {backup_path}")

    # 写入
    分类库路径.write_text(
        json.dumps(kb, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"写入: {分类库路径}")
    print(f"  分类总数: {len(kb['categories'])}")
    print(f"  别名总数: {kb['summary']['alias_count']}")
    print(f"  索引条目: {kb['summary']['name_index_count']}")
    print(f"  版本: {kb['version']}")

    print(f"\n{'=' * 60}")
    print("合并完成!")
    print(f"{'=' * 60}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
