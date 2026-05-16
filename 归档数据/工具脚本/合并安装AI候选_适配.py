#!/usr/bin/env python3
"""适配器：从 过程数据/AI候选_安装/*.json 直接合并到默认值库，跳过 JSONL 中间步骤。"""
import json, sys, time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "工具脚本"))

from 合并AI候选到默认值库 import (
    merge, ensure_phase_matrix, write_json, read_json,
    APPENDIX_TO_PHASE, DEFAULT_KB, BACKUP_DIR,
)

CANDIDATE_DIR = ROOT / "过程数据" / "AI候选_安装"
CHECKPOINT = CANDIDATE_DIR / "checkpoint.json"


def load_from_json_files():
    """从独立的 JSON 文件加载已完成批次的候选数据。"""
    ck = json.loads(CHECKPOINT.read_text(encoding="utf-8"))
    candidates = []
    stats = {"completed": 0, "skipped": 0, "failed": 0, "no_candidate": 0}

    for key, info in ck.get("batches", {}).items():
        status = info.get("status", "")
        if status != "completed":
            stats["failed" if status == "failed" else "skipped"] += 1
            continue

        output_file = info.get("output_file", "")
        path = Path(output_file) if output_file else CANDIDATE_DIR / f"{key}.json"

        if not path.exists():
            # Try alternate naming
            alt = CANDIDATE_DIR / f"{key}.json"
            if alt.exists():
                path = alt
            else:
                print(f"  ⚠ 文件不存在: {path}")
                continue

        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"  ⚠ 读取失败 {path.name}: {e}")
            continue

        # Skip if validation issues
        if data.get("validation_issues"):
            print(f"  ⚠ 验证问题 {key}: {data.get('validation_issues','')[:80]}")
            continue

        batch = data.get("batch", {})
        candidate = data.get("candidate", {})
        if not candidate:
            stats["no_candidate"] += 1
            continue

        systems = candidate.get("systems", [])
        recs = candidate.get("material_recommendations", [])
        if not systems and not recs:
            stats["no_candidate"] += 1
            continue

        candidates.append({
            "batch_id": batch.get("batch_id", key),
            "target_trade": batch.get("target_trade", candidate.get("trade", "")),
            "target_appendix": batch.get("target_appendix", candidate.get("appendix_name", info.get("appendix", ""))),
            "systems": systems,
            "recs": recs,
        })
        stats["completed"] += 1

    print(f"统计: 完成{stats['completed']} | 失败/跳过{stats['failed']+stats['skipped']} | 无候选{stats['no_candidate']}")
    return candidates


def main():
    print("=" * 60)
    print("安装AI候选(JSON文件) → 项目默认值库 合并")
    print("=" * 60)

    candidates = load_from_json_files()
    print(f"\n加载成功候选: {len(candidates)} 个批次")
    total_sys = sum(len(c["systems"]) for c in candidates)
    total_rec = sum(len(c["recs"]) for c in candidates)
    print(f"  系统: {total_sys} | 材料推荐: {total_rec}")

    result = merge(candidates, DEFAULT_KB)
    print(f"\n合并结果:")
    print(f"  新增系统: {len(result['new_systems'])}")
    print(f"  新增材料: {len(result['new_recs'])}")
    print(f"  新增 phase: {sorted(result['new_phases'])}")
    print(f"  重复跳过 - 系统: {result['dup_systems']} | 材料: {result['dup_recs']}")

    if not result["new_systems"] and not result["new_recs"]:
        print("\n无新数据，跳过写入。")
        return 0

    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y%m%d_%H%M%S")
    backup_path = BACKUP_DIR / f"项目默认值库_backup_install_merge_{ts}.json"
    write_json(backup_path, read_json(DEFAULT_KB))
    print(f"\n备份: {backup_path}")

    kb = read_json(DEFAULT_KB)
    kb.setdefault("default_systems", [])
    kb.setdefault("material_recommendations", [])

    ensure_phase_matrix(kb, result["new_phases"])

    if result["new_systems"]:
        kb["default_systems"].append({
            "_ai_candidate_block": True,
            "_note": "以下为安装工程AI生成的默认材料体系候选",
            "_generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        })
        kb["default_systems"].extend(result["new_systems"])

    if result["new_recs"]:
        kb["material_recommendations"].append({
            "_ai_candidate_block": True,
            "_note": "以下为安装工程AI生成的默认材料推荐候选",
            "_generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        })
        kb["material_recommendations"].extend(result["new_recs"])

    kb["meta"]["version"] = f"{kb['meta']['version'].split('+')[0]}+install_ai_{ts}"
    kb["meta"]["last_update_reason"] = f"合并安装工程AI候选: {len(result['new_systems'])} 系统 + {len(result['new_recs'])} 材料推荐"
    kb["meta"]["generated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    changelog = kb["meta"].setdefault("changelog", [])
    changelog.append(f"v{kb['meta']['version']}: 安装工程AI候选 — {len(result['new_systems'])}系统+{len(result['new_recs'])}推荐")

    write_json(DEFAULT_KB, kb)
    print(f"写入: {DEFAULT_KB}")
    print(f"\n知识库版本: {kb['meta']['version']}")
    print(f"  default_systems: {len(kb['default_systems'])}")
    print(f"  material_recommendations: {len(kb['material_recommendations'])}")
    print(f"  phase_matrix: {len(kb.get('phase_matrix', {}))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
