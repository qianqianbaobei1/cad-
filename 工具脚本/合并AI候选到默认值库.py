#!/usr/bin/env python3
"""AI补全候选合并到默认值库 — 将 AI 生成的候选材料批量合并到项目默认值库"""
import json
import csv
import re
import sys
from pathlib import Path
from datetime import datetime

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "项目数据"
LOG_DIR = ROOT / "过程数据"

# 默认值库路径
DEFAULT_DB_PATH = DATA_DIR / "项目默认值库.json"
CANDIDATE_PATH = DATA_DIR / "材料名库.json"


def load_json(path: Path, default=None):
    if not path.exists():
        return default
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save_json(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _归一(s: str) -> str:
    s = str(s or "").strip().lower()
    s = re.sub(r"[（(][^)）]*[)）]", "", s)
    s = re.sub(r"[^\w一-鿿]", "", s)
    return s


def merge_candidates(dry_run: bool = True, min_confidence: float = 0.6) -> dict:
    """将 AI 候选材料合并到默认值库"""
    default_db = load_json(DEFAULT_DB_PATH, {"materials": {}, "meta": {}})
    candidates = load_json(CANDIDATE_PATH, {"candidates": []})

    if not candidates.get("candidates"):
        return {"merged": 0, "message": "没有候选材料"}

    results = {"merged": 0, "skipped": 0, "conflicts": [], "new_materials": []}
    materials = default_db.setdefault("materials", {})

    for cand in candidates["candidates"]:
        name = cand.get("material_name", "").strip()
        if not name:
            continue

        confidence = cand.get("confidence", 0)
        if isinstance(confidence, str):
            confidence = {"high": 1.0, "medium": 0.7, "low": 0.3}.get(confidence, 0.5)

        if confidence < min_confidence:
            results["skipped"] += 1
            continue

        norm_name = _归一(name)
        # 查找是否已存在
        existing_key = None
        for key in materials:
            if _归一(key) == norm_name:
                existing_key = key
                break

        if existing_key:
            # 更新已有条目
            existing = materials[existing_key]
            if cand.get("unit") and not existing.get("unit"):
                existing["unit"] = cand["unit"]
            if cand.get("supply") and not existing.get("supply"):
                existing["supply"] = cand["supply"]
            existing["updated_at"] = datetime.now().isoformat()
            results["conflicts"].append({"name": name, "action": "updated"})
        else:
            if not dry_run:
                materials[name] = {
                    "material_name": name,
                    "unit": cand.get("unit", ""),
                    "supply": cand.get("supply", "乙供"),
                    "role": cand.get("role", "主材"),
                    "confidence": confidence,
                    "source": cand.get("source", "AI候选"),
                    "created_at": datetime.now().isoformat(),
                }
            results["merged"] += 1
            results["new_materials"].append(name)

    if not dry_run:
        default_db["meta"]["last_merge"] = datetime.now().isoformat()
        default_db["meta"]["total_materials"] = len(materials)
        save_json(DEFAULT_DB_PATH, default_db)

    return results


def main():
    dry_run = "--apply" not in sys.argv
    if dry_run:
        print(">>> DRY RUN 模式（不实际写入），加 --apply 执行合并")

    results = merge_candidates(dry_run=dry_run)
    print(f"\n合并结果:")
    print(f"  新增: {results['merged']}")
    print(f"  跳过(低可信度): {results['skipped']}")
    print(f"  更新: {len(results['conflicts'])}")
    if results["new_materials"]:
        print(f"\n新增材料 ({len(results['new_materials'])}个):")
        for name in results["new_materials"][:20]:
            print(f"  + {name}")


if __name__ == "__main__":
    main()
