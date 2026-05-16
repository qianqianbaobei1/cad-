#!/usr/bin/env python3
"""Merge validated AI candidate systems/materials into 项目默认值库.json.

Conservative merge rules:
- Never overwrites existing entries (matched by system_id / rec_id).
- AI candidates always get source_type=project_default_kb_ai_candidate.
- AI candidates always get confidence=medium, can_auto_add=false.
- Only merges successful batches (no errors, no validation_issues).
- Auto-infers phase from appendix_name for matching compatibility.
"""
from __future__ import annotations

import json
import time
from collections import OrderedDict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
JSONL = ROOT / "项目数据" / "AI候选_房建安装默认材料体系.jsonl"
DEFAULT_KB = ROOT / "项目数据" / "项目默认值库.json"
BACKUP_DIR = ROOT / "项目数据" / "备份"

# appendix_name → short phase name for matching
APPENDIX_TO_PHASE = {
    # 房建
    "土石方工程": "土石方工程",
    "地基处理与边坡支护工程": "地基处理工程",
    "桩 基 工 程": "桩基工程",
    "砌 筑 工 程": "主体结构工程",
    "混凝土及钢筋混凝土工程": "主体结构工程",
    "金属结构工程": "主体结构工程",
    "木结构工程": "主体结构工程",
    "屋面及防水工程": "防水工程",
    "保温、隔热、防腐工程": "保温工程",
    "门 窗 工 程": "门窗工程",
    "楼地面装饰工程": "粗装修工程",
    "墙、柱面装饰与隔断、幕墙工程": "外立面装饰",
    "天 棚 工 程": "精装修工程",
    "油漆、涂料、裱糊工程": "精装修工程",
    "其他装饰工程": "精装修工程",
    "措 施 项 目": "措施项目",
    # 安装
    "电气设备安装工程": "电气安装工程",
    "给排水、采暖、燃气工程": "给排水安装工程",
    "消防工程": "消防工程",
    "通风空调工程": "暖通安装工程",
    "建筑智能化工程": "智能化安装工程",
    "工业管道工程": "工业管道工程",
    "机械设备安装工程": "机械设备安装工程",
    "热力设备安装工程": "热力设备安装工程",
    "自动化控制仪表安装工程": "自动化控制安装工程",
    "通信设备及线路工程": "通信设备安装工程",
    "静置设备与工艺金属结构制作安装工程": "静置设备安装工程",
    "刷油、防腐蚀、绝热工程": "防腐绝热工程",
    "其他及附属工程": "安装附属工程",
}

# Phase → primary dimensions for phase_matrix
PHASE_DIMENSIONS = {
    "土石方工程": ["project_category"],
    "地基处理工程": ["project_category"],
    "桩基工程": ["project_category", "height_scope"],
    "主体结构工程": ["structure_type", "height_scope"],
    "防水工程": ["location"],
    "保温工程": ["location"],
    "门窗工程": ["project_category"],
    "粗装修工程": ["location"],
    "外立面装饰": ["project_category"],
    "精装修工程": ["project_category"],
    "措施项目": [],
    "电气安装工程": ["project_category"],
    "给排水安装工程": ["project_category"],
    "消防工程": ["project_category"],
    "暖通安装工程": ["project_category"],
    "智能化安装工程": ["project_category"],
    "工业管道工程": ["project_category"],
    "机械设备安装工程": ["project_category"],
    "热力设备安装工程": ["project_category"],
    "自动化控制安装工程": ["project_category"],
    "通信设备安装工程": ["project_category"],
    "静置设备安装工程": ["project_category"],
    "防腐绝热工程": ["project_category"],
    "安装附属工程": [],
}


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write_json(path: Path, data: Any) -> None:
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def infer_phase(appendix_name: str) -> str:
    """Map Q0 appendix_name to KB phase name."""
    return APPENDIX_TO_PHASE.get(appendix_name, appendix_name)


def load_ai_candidates(jsonl_path: Path) -> list[dict]:
    """Load only successful candidates from JSONL."""
    candidates: list[dict] = []
    seen_batch_ids: set[str] = set()
    for line in jsonl_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if row.get("error") or row.get("validation_issues"):
            continue
        batch = row.get("batch", {})
        bid = batch.get("batch_id", "")
        if bid in seen_batch_ids:
            continue
        seen_batch_ids.add(bid)
        candidate = row.get("candidate", {})
        candidates.append({
            "batch_id": bid,
            "target_trade": batch.get("target_trade", ""),
            "target_appendix": batch.get("target_appendix", ""),
            "systems": candidate.get("systems", []),
            "recs": candidate.get("material_recommendations", []),
        })
    return candidates


def merge(candidates: list[dict], kb_path: Path) -> dict:
    """Merge AI candidates into the knowledge base."""
    if not kb_path.exists():
        raise FileNotFoundError(f"知识库不存在: {kb_path}")

    kb = read_json(kb_path)

    existing_systems: set[str] = {
        s.get("system_id", "")
        for s in kb.get("default_systems", [])
        if s.get("system_id")
    }
    existing_recs: set[str] = {
        r.get("rec_id", "")
        for r in kb.get("material_recommendations", [])
        if r.get("rec_id")
    }

    new_systems: list[dict] = []
    new_recs: list[dict] = []
    new_phases: set[str] = set()
    dup_system_count = 0
    dup_rec_count = 0

    for c in candidates:
        trade = c.get("target_trade", "")
        appendix = c.get("target_appendix", "")
        inferred_phase = infer_phase(appendix)
        new_phases.add(inferred_phase)

        for s in c["systems"]:
            if not isinstance(s, dict):
                continue
            sid = s.get("system_id", "")
            if not sid:
                continue
            if sid in existing_systems:
                dup_system_count += 1
                continue
            existing_systems.add(sid)
            # Normalize fields for pipeline compatibility
            s["phase"] = s.get("phase") or inferred_phase
            s["trade"] = s.get("trade") or trade
            s["appendix_name"] = s.get("appendix_name") or appendix
            s["source_type"] = "project_default_kb_ai_candidate"
            s["confidence"] = "medium"
            s["can_auto_add_materials"] = False
            s["must_yield_to_feature_text"] = True
            s["must_pass_t3"] = True
            s["requires_review_if_not_in_feature"] = True
            s.setdefault("applicable_structure_type", [])
            s.setdefault("applicable_height_scope", [])
            s.setdefault("typical_scene", "")
            new_systems.append(s)

        for r in c["recs"]:
            if not isinstance(r, dict):
                continue
            rid = r.get("rec_id", "")
            if not rid:
                continue
            if rid in existing_recs:
                dup_rec_count += 1
                continue
            existing_recs.add(rid)
            r["source_type"] = "project_default_kb_ai_candidate"
            r["confidence"] = "medium"
            r["can_auto_add"] = False
            r["can_auto_pass_when_default_used"] = False
            r["requires_review_if_not_in_feature"] = True
            r.setdefault("quantity_rule", {})
            r.setdefault("default_spec_values", {})
            r.setdefault("loss_rate_hint", None)
            r.setdefault("unit_hint", "")
            r.setdefault("required_params", [])
            r.setdefault("spec_params", {})
            new_recs.append(r)

    return {
        "new_systems": new_systems,
        "new_recs": new_recs,
        "new_phases": new_phases,
        "dup_systems": dup_system_count,
        "dup_recs": dup_rec_count,
    }


def ensure_phase_matrix(kb: dict, new_phases: set[str]) -> None:
    """Add phase_matrix entries for any new phases introduced by AI systems."""
    matrix = kb.setdefault("phase_matrix", {})
    existing_phases = set(matrix.keys())
    for phase in new_phases:
        if phase in existing_phases:
            continue
        dims = PHASE_DIMENSIONS.get(phase, ["project_category"])
        matrix[phase] = {
            "primary_dimensions": dims,
            "query_keys": dims,
            "default_structure_type": {},
        }
        existing_phases.add(phase)


def main() -> int:
    print("=" * 60)
    print("AI候选 → 项目默认值库 合并")
    print("=" * 60)

    candidates = load_ai_candidates(JSONL)
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
    backup_path = BACKUP_DIR / f"项目默认值库_backup_{ts}.json"
    write_json(backup_path, read_json(DEFAULT_KB))
    print(f"\n备份: {backup_path}")

    kb = read_json(DEFAULT_KB)
    kb.setdefault("default_systems", [])
    kb.setdefault("material_recommendations", [])

    # Ensure phase_matrix has entries for all new phases
    ensure_phase_matrix(kb, result["new_phases"])

    if result["new_systems"]:
        kb["default_systems"].append({
            "_ai_candidate_block": True,
            "_note": "以下为 DeepSeek AI 生成的默认材料体系候选 — 已自动推断 phase 字段",
            "_generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        })
        kb["default_systems"].extend(result["new_systems"])

    if result["new_recs"]:
        kb["material_recommendations"].append({
            "_ai_candidate_block": True,
            "_note": "以下为 DeepSeek AI 生成的默认材料推荐候选",
            "_generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        })
        kb["material_recommendations"].extend(result["new_recs"])

    kb["meta"]["version"] = f"{kb['meta']['version'].split('+')[0]}+ai_candidates_{ts}"
    kb["meta"]["last_update_reason"] = f"合并 DeepSeek AI 候选默认材料体系(含phase推断): {len(result['new_systems'])} 系统 + {len(result['new_recs'])} 材料推荐"
    kb["meta"]["generated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    changelog = kb["meta"].setdefault("changelog", [])
    changelog.append(f"v{kb['meta']['version']}: AI生成候选(含phase推断) — {len(result['new_systems'])} 系统 + {len(result['new_recs'])} 推荐 + {len(result['new_phases'])} 新phase")

    write_json(DEFAULT_KB, kb)
    print(f"写入: {DEFAULT_KB}")

    print(f"\n{'=' * 60}")
    print(f"合并完成!")
    print(f"  知识库版本: {kb['meta']['version']}")
    print(f"  default_systems 总数: {len(kb['default_systems'])}")
    print(f"  material_recommendations 总数: {len(kb['material_recommendations'])}")
    print(f"  phase_matrix 条目: {len(kb.get('phase_matrix', {}))}")
    print(f"{'=' * 60}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
