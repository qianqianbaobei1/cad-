#!/usr/bin/env python3
"""R6: 数据一致性校验规则 — 12条自动检查规则。

每次数据更新后运行，自动检测异常并标记。

用法：
  python3 工具脚本/校验数据一致性.py           # 全部检查
  python3 工具脚本/校验数据一致性.py --json     # JSON 格式输出
  python3 工具脚本/校验数据一致性.py --rule R1  # 仅运行指定规则
"""

from __future__ import annotations

import csv
import json
import re
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "标准知识库" / "源数据" / "01_定额库"
IDX = ROOT / "项目数据" / "本地知识库包" / "索引"
T3_FJ = ROOT / "标准知识库" / "T3_房建_标准物料库.csv"
T3_AZ = ROOT / "标准知识库" / "T3_安装_标准物料库.csv"
OUTPUT = ROOT / "过程数据" / "数据一致性校验报告.json"


# ══════════════════════════════════════
# 12 条校验规则
# ══════════════════════════════════════

RULES = {
    "R1": "Q1.boq_code 必须在 Q0 中存在",
    "R2": "Q1.quota_id 必须在 Q2 中有对应条目",
    "R3": "Q2.material_id 必须在 T3 物料库中存在",
    "R4": "Q0 编码不得重复（同编码不同分部例外）",
    "R5": "Q0.分部编码必须在附录中存在对应章节",
    "R6": "T3.material_id 不得重复",
    "R7": "T3.别名 JSON 格式必须有效",
    "R8": "Q3.material_id 必须在 T3 中存在",
    "R9": "项目默认值库.system_id 不得重复",
    "R10": "项目默认值库.material_name 不得为空",
    "R11": "Q2 中存在 material_id 的条目数占比不得低于 10%",
    "R12": "T2.boq_code 非空时必须在 Q0 中存在",
}


def _load_csv(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with open(path, "r", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def _load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def check_R1() -> dict:
    """Q1.boq_code 必须在 Q0 中存在。"""
    q0_az = _load_csv(SRC / "Q0_清单项目编码_安装工程.csv")
    q0_fj = _load_csv(SRC / "Q0_清单项目编码_房建工程.csv")
    q0_codes = set()
    for r in q0_az + q0_fj:
        c = r.get("项目编码", "").strip()
        if c:
            q0_codes.add(c)

    q1 = _load_csv(SRC / "Q1_定额索引.csv")
    invalid = []
    for r in q1:
        bc = r.get("boq_code", "").strip()
        if bc and bc not in q0_codes:
            invalid.append({"quota_id": r.get("quota_id", ""), "boq_code": bc})

    return {
        "rule": "R1",
        "desc": RULES["R1"],
        "total": len([r for r in q1 if r.get("boq_code", "").strip()]),
        "invalid": len(invalid),
        "samples": invalid[:10],
        "status": "pass" if len(invalid) == 0 else "fail",
    }


def check_R2() -> dict:
    """Q1.quota_id 必须在 Q2 中有对应条目。"""
    q1 = _load_csv(SRC / "Q1_定额索引.csv")
    q2_idx = _load_json(IDX / "q2_按定额ID.json")

    missing = []
    for r in q1:
        qid = r.get("quota_id", "").strip()
        if qid and qid not in q2_idx:
            missing.append(qid)

    return {
        "rule": "R2",
        "desc": RULES["R2"],
        "total_q1": len(q1),
        "q1_with_quota": len([r for r in q1 if r.get("quota_id", "").strip()]),
        "without_q2": len(missing),
        "pct": f"{len(missing)/max(len(q1),1)*100:.2f}%",
        "samples": missing[:10],
        "status": "pass" if len(missing) < len(q1) * 0.01 else "warn",
    }


def check_R3() -> dict:
    """Q2.material_id 必须在 T3 中存在。"""
    t3_ids = set()
    for csv_path in [T3_FJ, T3_AZ]:
        for r in _load_csv(csv_path):
            mid = r.get("物料ID", "").strip()
            if mid:
                t3_ids.add(mid)

    q2 = _load_csv(SRC / "Q2_定额材料消耗.csv")
    invalid = set()
    total_with_id = 0
    for r in q2:
        mid = r.get("material_id", "").strip()
        if mid:
            total_with_id += 1
            if mid not in t3_ids:
                invalid.add(mid)

    return {
        "rule": "R3",
        "desc": RULES["R3"],
        "q2_total": len(q2),
        "q2_with_id": total_with_id,
        "invalid_ids": len(invalid),
        "invalid_pct": f"{len(invalid)/max(total_with_id,1)*100:.2f}%",
        "samples": list(invalid)[:10],
        "status": "pass" if len(invalid) == 0 else "fail",
    }


def check_R4() -> dict:
    """Q0 编码不得重复（同编码不同分部例外）。"""
    q0_az = _load_csv(SRC / "Q0_清单项目编码_安装工程.csv")
    codes = defaultdict(list)
    for r in q0_az:
        c = r.get("项目编码", "").strip()
        if c:
            codes[c].append(r.get("项目名称", "").strip())

    dupes = {c: names for c, names in codes.items() if len(names) > 1}
    return {
        "rule": "R4",
        "desc": RULES["R4"],
        "total_codes": len(codes),
        "duplicate_count": len(dupes),
        "duplicates": {c: names for c, names in list(dupes.items())[:5]},
        "status": "warn" if dupes else "pass",
    }


def check_R6() -> dict:
    """T3.material_id 不得重复。"""
    ids = []
    for csv_path in [T3_FJ, T3_AZ]:
        for r in _load_csv(csv_path):
            mid = r.get("物料ID", "").strip()
            if mid:
                ids.append(mid)
    dupes = [mid for mid, cnt in Counter(ids).items() if cnt > 1]
    return {
        "rule": "R6",
        "desc": RULES["R6"],
        "total": len(ids),
        "duplicates": dupes,
        "status": "pass" if not dupes else "fail",
    }


def check_R7() -> dict:
    """T3 别名 JSON 格式必须有效。"""
    invalid = []
    total = 0
    for csv_path in [T3_FJ, T3_AZ]:
        for r in _load_csv(csv_path):
            total += 1
            raw = r.get("别名", "").strip()
            if raw and raw not in ("[]", "null", ""):
                try:
                    parsed = json.loads(raw)
                    if not isinstance(parsed, list):
                        invalid.append({"id": r.get("物料ID", ""), "raw": raw[:100], "error": "not a list"})
                except json.JSONDecodeError as e:
                    invalid.append({"id": r.get("物料ID", ""), "raw": raw[:100], "error": str(e)})

    return {
        "rule": "R7",
        "desc": RULES["R7"],
        "total": total,
        "invalid": len(invalid),
        "samples": invalid[:5],
        "status": "pass" if len(invalid) == 0 else "fail",
    }


def check_R8() -> dict:
    """Q3.material_id 必须在 T3 中存在。"""
    t3_ids = set()
    for csv_path in [T3_FJ, T3_AZ]:
        for r in _load_csv(csv_path):
            mid = r.get("物料ID", "").strip()
            if mid:
                t3_ids.add(mid)

    q3 = _load_csv(SRC / "Q3_定额材料映射.csv")
    invalid = set()
    for r in q3:
        mid = r.get("material_id", "").strip()
        if mid and mid not in t3_ids:
            invalid.add(mid)

    return {
        "rule": "R8",
        "desc": RULES["R8"],
        "q3_total": len(q3),
        "invalid_ids": len(invalid),
        "samples": list(invalid)[:10],
        "status": "pass" if len(invalid) == 0 else "fail",
    }


def check_R9() -> dict:
    """项目默认值库.system_id 不得重复。"""
    default_kb = _load_json(ROOT / "项目数据" / "项目默认值库.json")
    systems = default_kb.get("default_systems", [])
    sids = [s.get("system_id", "") for s in systems if s.get("system_id")]
    dupes = [sid for sid, cnt in Counter(sids).items() if cnt > 1]
    return {
        "rule": "R9",
        "desc": RULES["R9"],
        "total": len(sids),
        "duplicates": dupes,
        "status": "pass" if not dupes else "fail",
    }


def check_R10() -> dict:
    """项目默认值库.material_name 不得为空。"""
    default_kb = _load_json(ROOT / "项目数据" / "项目默认值库.json")
    recs = default_kb.get("material_recommendations", [])
    empty = [r.get("rec_id", "?") for r in recs if not r.get("material_name", "").strip()]
    return {
        "rule": "R10",
        "desc": RULES["R10"],
        "total": len(recs),
        "empty_count": len(empty),
        "samples": empty[:10],
        "status": "pass" if len(empty) == 0 else "fail",
    }


def check_R11() -> dict:
    """Q2 中存在 material_id 的条目数占比不得低于 10%。"""
    q2 = _load_csv(SRC / "Q2_定额材料消耗.csv")
    with_id = sum(1 for r in q2 if r.get("material_id", "").strip())
    pct = with_id / max(len(q2), 1) * 100
    return {
        "rule": "R11",
        "desc": RULES["R11"],
        "total": len(q2),
        "with_id": with_id,
        "pct": f"{pct:.2f}%",
        "status": "pass" if pct >= 10 else "warn",
    }


def check_R12() -> dict:
    """T2.boq_code 非空时必须在 Q0 中存在。"""
    q0_az = _load_csv(SRC / "Q0_清单项目编码_安装工程.csv")
    q0_fj = _load_csv(SRC / "Q0_清单项目编码_房建工程.csv")
    q0_codes = set()
    for r in q0_az + q0_fj:
        c = r.get("项目编码", "").strip()
        if c:
            q0_codes.add(c)

    t2 = _load_csv(SRC / "T2_清单材料映射库.csv")
    invalid = set()
    for r in t2:
        bc = r.get("boq_code", "").strip()
        if bc and bc not in q0_codes:
            invalid.add(bc)

    return {
        "rule": "R12",
        "desc": RULES["R12"],
        "total_with_code": sum(1 for r in t2 if r.get("boq_code", "").strip()),
        "invalid": len(invalid),
        "samples": list(invalid)[:10],
        "status": "pass" if len(invalid) == 0 else "fail",
    }


ALL_CHECKS = {
    "R1": check_R1, "R2": check_R2, "R3": check_R3, "R4": check_R4,
    "R6": check_R6, "R7": check_R7, "R8": check_R8, "R9": check_R9,
    "R10": check_R10, "R11": check_R11, "R12": check_R12,
    # R5 需人工判定，暂不自动执行
}


def main():
    import argparse
    parser = argparse.ArgumentParser(description="数据一致性校验")
    parser.add_argument("--rule", type=str, help="仅运行指定规则 (R1-R12)")
    parser.add_argument("--json", action="store_true", help="JSON 输出")
    parser.add_argument("--all", action="store_true", help="运行所有规则")
    args = parser.parse_args()

    if not args.json:
        print("=" * 60)
        print("R6: 数据一致性校验")
        print(f"时间: {datetime.now().isoformat()}")
        print("=" * 60)

    checks_to_run = [args.rule] if args.rule else list(ALL_CHECKS.keys())
    results = []

    for rule_id in checks_to_run:
        if rule_id not in ALL_CHECKS:
            continue
        if not args.json:
            print(f"\n[{rule_id}] {RULES.get(rule_id, '?')}")
        try:
            result = ALL_CHECKS[rule_id]()
            results.append(result)
            if not args.json:
                status_icon = "✅" if result["status"] == "pass" else ("⚠️" if result["status"] == "warn" else "❌")
                print(f"  {status_icon} {result['status']}")
                for k, v in result.items():
                    if k not in ("rule", "desc", "status", "samples"):
                        print(f"    {k}: {v}")
                if result.get("samples"):
                    print(f"    samples: {result['samples'][:3]}")
        except Exception as e:
            results.append({"rule": rule_id, "status": "error", "error": str(e)})
            if not args.json:
                print(f"  ❌ error: {e}")

    # Summary
    passed = sum(1 for r in results if r["status"] == "pass")
    warned = sum(1 for r in results if r["status"] == "warn")
    failed = sum(1 for r in results if r["status"] == "fail")
    errors = sum(1 for r in results if r["status"] == "error")

    summary = {
        "timestamp": datetime.now().isoformat(),
        "total_rules": len(results),
        "passed": passed, "warned": warned, "failed": failed, "errors": errors,
        "score": f"{passed}/{len(results)}",
        "results": results,
    }

    # Save report
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    else:
        print(f"\n{'='*60}")
        print(f"总结: {passed}通过 / {warned}警告 / {failed}失败 / {errors}错误")
        print(f"报告: {OUTPUT}")
        print(f"{'='*60}")


if __name__ == "__main__":
    main()
