#!/usr/bin/env python3
"""Validate AI-generated candidate data against Q0 scope and engineering rules."""
from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
Q0_FJ = ROOT / "项目数据" / "本地知识库包" / "sources" / "01_定额库" / "Q0_清单项目编码_房建工程.json"
Q0_AZ = ROOT / "项目数据" / "本地知识库包" / "sources" / "01_定额库" / "Q0_清单项目编码_安装工程.json"
JSONL = ROOT / "项目数据" / "AI候选_房建安装默认材料体系.jsonl"
T3_FJ = ROOT / "标准知识库" / "T3_房建_标准物料库.csv"
T3_AZ = ROOT / "标准知识库" / "T3_安装_标准物料库.csv"

VALID_ROLES = {"主材", "辅材", "周转材料", "措施材料"}

# Known real GB standards for spot-checking
KNOWN_GB_PREFIXES = {
    # 国家标准
    "GB", "GB/T", "GBJ", "GBZ",
    # 建筑工程行业
    "JGJ", "JGJ/T", "JG", "JG/T",
    # 城镇建设行业
    "CJJ", "CJJ/T", "CJ", "CJ/T",
    # 建材行业
    "JC", "JC/T",
    # 工程建设标准化协会
    "CECS",
    # 各行业标准
    "SH", "SY", "HG", "HG/T", "YB", "YB/T", "DL", "DL/T",
    "NB", "SL", "QB", "QB/T", "JB", "JB/T",
    # 图集
    "图集",
}


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def load_q0_scope() -> set[tuple[str, str, str]]:
    """Return set of (trade, appendix_name, section_code) from Q0 files."""
    scope: set[tuple[str, str, str]] = set()
    for q0_path in [Q0_FJ, Q0_AZ]:
        data = read_json(q0_path)
        rows = data.get("rows", data if isinstance(data, list) else [])
        for r in rows:
            scope.add((
                r.get("专业工程名称", ""),
                r.get("附录名称", ""),
                r.get("分部编码", ""),
            ))
    return scope


def load_t3_materials() -> set[str]:
    """Load T3 standard material names from both building and MEP CSVs."""
    import csv
    names: set[str] = set()
    for csv_path in [T3_FJ, T3_AZ]:
        if not csv_path.exists():
            continue
        with csv_path.open(encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            for row in reader:
                name = row.get("material_name", "") or row.get("物料名称", "") or row.get("标准名称", "")
                if name:
                    names.add(name.strip())
    return names


def load_successful_batches() -> list[dict]:
    batches: list[dict] = []
    with JSONL.open(encoding="utf-8") as f:
        for i, line in enumerate(f, 1):
            if not line.strip():
                continue
            row = json.loads(line)
            if row.get("error") or row.get("validation_issues"):
                continue
            candidate = row.get("candidate", {})
            batch = row.get("batch", {})
            batches.append({
                "line": i,
                "batch": batch,
                "systems": candidate.get("systems", []),
                "recs": candidate.get("material_recommendations", []),
            })
    return batches


def validate_layer1(batches: list[dict], q0_scope: set) -> dict:
    """Structural compliance checks."""
    report: dict[str, Any] = {"errors": [], "warnings": []}
    all_system_ids: set[str] = set()
    all_rec_ids: set[str] = set()
    dup_system_ids: list[str] = []
    dup_rec_ids: list[str] = []

    for batch in batches:
        bid = batch["batch"].get("batch_id", "?")
        systems = batch["systems"]
        recs = batch["recs"]

        # Build system_id set for this batch
        sys_ids = {s.get("system_id", "") for s in systems if isinstance(s, dict)}

        for s in systems:
            if not isinstance(s, dict):
                continue
            sid = s.get("system_id", "")
            if not sid:
                report["errors"].append(f"[L1] {bid}: system missing system_id")
                continue
            if sid in all_system_ids:
                dup_system_ids.append(sid)
            all_system_ids.add(sid)

            # Q0 scope
            key = (s.get("trade", ""), s.get("appendix_name", ""), s.get("section_code", ""))
            if key not in q0_scope:
                report["errors"].append(
                    f"[L1] {bid} system {sid}: section not in Q0 → trade={key[0]}, "
                    f"appendix={key[1]}, section_code={key[2]}"
                )

            # Safety fields
            if s.get("confidence") == "high":
                report["errors"].append(f"[L1] {bid} system {sid}: confidence=high (must be medium)")
            if s.get("can_auto_add_materials") is True:
                report["errors"].append(f"[L1] {bid} system {sid}: can_auto_add_materials=true")

        for r in recs:
            if not isinstance(r, dict):
                continue
            rid = r.get("rec_id", "")
            if not rid:
                report["errors"].append(f"[L1] {bid}: rec missing rec_id")
                continue
            if rid in all_rec_ids:
                dup_rec_ids.append(rid)
            all_rec_ids.add(rid)

            # Reference integrity
            r_sid = r.get("system_id", "")
            if r_sid and r_sid not in sys_ids:
                report["errors"].append(
                    f"[L1] {bid} rec {rid}: system_id={r_sid} not found in batch systems"
                )

            # Safety fields
            if r.get("confidence") == "high":
                report["errors"].append(f"[L1] {bid} rec {rid}: confidence=high")
            if r.get("can_auto_add") is True:
                report["errors"].append(f"[L1] {bid} rec {rid}: can_auto_add=true")
            if r.get("can_auto_pass_when_default_used") is True:
                report["errors"].append(f"[L1] {bid} rec {rid}: can_auto_pass_when_default_used=true")

    if dup_system_ids:
        report["warnings"].append(f"[L1] Duplicate system_ids across batches: {dup_system_ids[:10]}")
    if dup_rec_ids:
        report["warnings"].append(f"[L1] Duplicate rec_ids across batches: {dup_rec_ids[:10]}")

    return report


def validate_layer2(batches: list[dict], t3_names: set[str]) -> dict:
    """Content quality checks."""
    report: dict[str, Any] = {"errors": [], "warnings": [], "stats": {}}
    empty_spec: list[str] = []
    empty_material_name: list[str] = []
    bad_role: list[str] = []
    empty_system_name: list[str] = []
    no_main_material: list[str] = []
    too_many_review_notes: list[str] = []
    fake_standards: list[tuple[str, str, str]] = []
    no_t3_match: list[str] = []

    for batch in batches:
        bid = batch["batch"].get("batch_id", "?")

        for s in batch["systems"]:
            if not isinstance(s, dict):
                continue
            sid = s.get("system_id", "")
            sname = s.get("system_name", "")
            if not sname or sname.strip() == "":
                empty_system_name.append(f"{bid} {sid}")

        role_count: dict[str, int] = defaultdict(int)
        for r in batch["recs"]:
            if not isinstance(r, dict):
                continue
            rid = r.get("rec_id", "")

            # Material name
            mname = r.get("material_name", "")
            if not mname or mname.strip() == "":
                empty_material_name.append(f"{bid} {rid}")
                continue

            # Role
            role = r.get("role", "")
            if role not in VALID_ROLES:
                bad_role.append(f"{bid} {rid}: role='{role}', material={mname}")
            else:
                role_count[role] = role_count.get(role, 0) + 1

            # Spec
            spec = r.get("typical_spec", "")
            if not spec or spec.strip() == "" or spec.strip() in {"待定", "TBD", "N/A", "-"}:
                empty_spec.append(f"{bid} {rid}: {mname}")

            # Review notes
            notes = r.get("review_notes", [])
            if isinstance(notes, list) and len(notes) > 3:
                too_many_review_notes.append(f"{bid} {rid}: {mname} ({len(notes)} notes)")

            # Basis standards check
            basis = r.get("basis", [])
            for b in (basis or []):
                if _looks_fake_standard(b):
                    fake_standards.append((bid, rid, b))

            # T3 match
            if t3_names and not _has_t3_match(mname, t3_names):
                no_t3_match.append(f"{bid} {rid}: {mname}")

        # Check if any system lacks 主材
        for s in batch["systems"]:
            if not isinstance(s, dict):
                continue
            sid = s.get("system_id", "")
            sys_recs = [r for r in batch["recs"] if isinstance(r, dict) and r.get("system_id") == sid]
            if sys_recs and not any(r.get("role") == "主材" for r in sys_recs):
                no_main_material.append(f"{bid} {sid}: {s.get('system_name', '?')}")

    if empty_spec:
        report["warnings"].append(f"[L2] {len(empty_spec)} materials with empty/none spec")
        report["stats"]["empty_spec_sample"] = empty_spec[:10]
    if empty_material_name:
        report["errors"].append(f"[L2] {len(empty_material_name)} materials with empty name")
    if bad_role:
        report["errors"].append(f"[L2] {len(bad_role)} materials with invalid role")
        report["stats"]["bad_role_sample"] = bad_role[:10]
    if empty_system_name:
        report["errors"].append(f"[L2] {len(empty_system_name)} systems with empty system_name")
    if no_main_material:
        report["warnings"].append(f"[L2] {len(no_main_material)} systems without 主材")
        report["stats"]["no_main_material_sample"] = no_main_material[:10]
    if too_many_review_notes:
        report["warnings"].append(f"[L2] {len(too_many_review_notes)} materials with >3 review_notes")
    if fake_standards:
        report["warnings"].append(f"[L2] {len(fake_standards)} potentially fake standards in basis")
        report["stats"]["fake_standards_sample"] = fake_standards[:10]
    if no_t3_match and t3_names:
        report["warnings"].append(f"[L2] {len(no_t3_match)} materials without T3 library match")
        report["stats"]["no_t3_match_sample"] = no_t3_match[:15]

    return report


def _looks_fake_standard(std: str) -> bool:
    """Check if a standard reference looks plausible."""
    if not std or not isinstance(std, str):
        return False
    # Must start with a known prefix
    if not any(std.startswith(p) for p in KNOWN_GB_PREFIXES):
        return True
    # Must have a year or number pattern
    if not re.search(r"\d{4}", std) and not re.search(r"[\d.]+-?\d{4}", std):
        return False
    return False


def _has_t3_match(mname: str, t3_names: set[str]) -> bool:
    """Check if material name has a fuzzy match in T3."""
    if mname in t3_names:
        return True
    # Try removing parenthetical content
    cleaned = re.sub(r"[（(][^)）]*[)）]", "", mname).strip()
    if cleaned and cleaned != mname and cleaned in t3_names:
        return True
    return False


def coverage_analysis(batches: list[dict], q0_scope: set) -> dict:
    """Check which Q0 sections are covered vs missing."""
    covered_sections: dict[tuple, list[str]] = defaultdict(list)
    for batch in batches:
        for s in batch["systems"]:
            if not isinstance(s, dict):
                continue
            key = (s.get("trade", ""), s.get("appendix_name", ""), s.get("section_code", ""))
            covered_sections[key].append(s.get("system_id", ""))

    # Build Q0 sections grouped by appendix
    q0_by_appendix: dict[tuple[str, str], set[str]] = defaultdict(set)
    for trade, appendix, section in q0_scope:
        if not section:
            continue
        q0_by_appendix[(trade, appendix)].add(section)

    # Check coverage per appendix
    gaps: list[str] = []
    appendix_summary: list[dict] = []
    for (trade, appendix), sections in sorted(q0_by_appendix.items()):
        covered = {k[2] for k in covered_sections if k[0] == trade and k[1] == appendix and k[2]}
        missing = sections - covered
        if missing:
            gaps.append(f"{appendix}: missing {len(missing)}/{len(sections)} sections: {sorted(missing)}")
        appendix_summary.append({
            "trade": trade,
            "appendix": appendix,
            "total_sections": len(sections),
            "covered": len(sections & covered),
            "missing": len(missing),
            "missing_codes": sorted(missing) if missing else [],
        })

    return {
        "total_q0_sections": sum(len(s) for s in q0_by_appendix.values()),
        "covered_sections": len(covered_sections),
        "gaps": gaps,
        "appendix_details": appendix_summary,
    }


def summary_stats(batches: list[dict]) -> dict:
    """Aggregate statistics."""
    total_systems = 0
    total_recs = 0
    role_dist: dict[str, int] = Counter()
    trade_dist: dict[str, int] = Counter()
    appendix_dist: dict[str, int] = Counter()

    for batch in batches:
        systems = [s for s in batch["systems"] if isinstance(s, dict)]
        recs = [r for r in batch["recs"] if isinstance(r, dict)]
        total_systems += len(systems)
        total_recs += len(recs)
        for r in recs:
            role_dist[r.get("role", "unknown")] += 1
        for s in systems:
            trade_dist[s.get("trade", "?")] += 1
            appendix_dist[s.get("appendix_name", "?")] += 1

    return {
        "successful_batches": len(batches),
        "total_systems": total_systems,
        "total_recommendations": total_recs,
        "role_distribution": dict(role_dist.most_common()),
        "systems_by_trade": dict(trade_dist.most_common()),
        "systems_by_appendix": dict(appendix_dist.most_common()),
    }


def main() -> int:
    print("=" * 60)
    print("AI候选数据验证报告")
    print("=" * 60)

    # Load reference data
    print("\n加载参考数据...")
    q0_scope = load_q0_scope()
    print(f"  Q0 范围: {len(q0_scope)} 条 (trade, appendix, section)")
    t3_names = load_t3_materials()
    print(f"  T3 标准物料: {len(t3_names)} 条")

    # Load AI candidates
    batches = load_successful_batches()
    print(f"  AI候选: {len(batches)} 个成功批次")

    # Stats
    stats = summary_stats(batches)
    print(f"\n--- 概览 ---")
    print(f"  成功批次: {stats['successful_batches']}")
    print(f"  系统总数: {stats['total_systems']}")
    print(f"  材料推荐总数: {stats['total_recommendations']}")
    print(f"  Role 分布: {stats['role_distribution']}")
    print(f"  Trade 分布: {stats['systems_by_trade']}")

    # Layer 1
    print(f"\n--- Layer 1: 结构合规 ---")
    l1 = validate_layer1(batches, q0_scope)
    if l1["errors"]:
        print(f"  ❌ 错误 {len(l1['errors'])} 条:")
        for e in l1["errors"][:20]:
            print(f"     {e}")
        if len(l1["errors"]) > 20:
            print(f"     ... 还有 {len(l1['errors']) - 20} 条")
    else:
        print(f"  ✅ 无错误")
    if l1["warnings"]:
        print(f"  ⚠️  警告 {len(l1['warnings'])} 条:")
        for w in l1["warnings"]:
            print(f"     {w}")

    # Layer 2
    print(f"\n--- Layer 2: 内容质量 ---")
    l2 = validate_layer2(batches, t3_names)
    if l2["errors"]:
        print(f"  ❌ 错误 {len(l2['errors'])} 条:")
        for e in l2["errors"][:15]:
            print(f"     {e}")
        if len(l2["errors"]) > 15:
            print(f"     ... 还有 {len(l2['errors']) - 15} 条")
    else:
        print(f"  ✅ 无错误")
    if l2["warnings"]:
        print(f"  ⚠️  警告 {len(l2['warnings'])} 条:")
        for w in l2["warnings"]:
            print(f"     {w}")

    # Coverage
    print(f"\n--- Q0 覆盖度 ---")
    cov = coverage_analysis(batches, q0_scope)
    total_sections = cov["total_q0_sections"]
    covered = cov["covered_sections"]
    pct = covered / total_sections * 100 if total_sections else 0
    print(f"  Q0 总分部数: {total_sections}")
    print(f"  已覆盖: {covered} ({pct:.1f}%)")
    gap_count = sum(1 for g in cov["gaps"])
    if gap_count:
        print(f"  有缺失的附录: {gap_count} 个")
        for g in cov["gaps"][:10]:
            print(f"     {g[:120]}")
    else:
        print(f"  ✅ 全部分部已覆盖")

    # Appendix detail
    print(f"\n--- 附录明细 ---")
    for ad in cov["appendix_details"]:
        flag = "✅" if ad["missing"] == 0 else f"⚠️  缺{ad['missing']}个"
        print(f"  {flag} {ad['appendix']}: {ad['covered']}/{ad['total_sections']}")

    # Overall grade
    total_errors = len(l1["errors"]) + len(l2["errors"])
    total_warnings = len(l1["warnings"]) + len(l2["warnings"]) + gap_count
    if total_errors == 0 and total_warnings == 0:
        grade = "A+ — 完美通过"
    elif total_errors == 0:
        grade = "A — 无致命错误，有少量需关注项"
    elif total_errors <= 5:
        grade = "B — 有少量错误需修复"
    else:
        grade = f"C — {total_errors} 个错误需修复后再合并"

    print(f"\n{'=' * 60}")
    print(f"综合评级: {grade}")
    print(f"错误: {total_errors} | 警告: {total_warnings}")
    print(f"{'=' * 60}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
