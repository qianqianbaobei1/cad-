#!/usr/bin/env python3
"""Validate local JSON knowledge-base package completeness."""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = ROOT / "项目数据" / "本地知识库包" / "manifest.json"

REQUIRED_RUNTIME_BASENAMES = {
    "Q0_清单项目编码_房建工程.csv",
    "Q0_清单项目编码_安装工程.csv",
    "Q1_定额索引.csv",
    "Q2_定额材料消耗.csv",
    "Q3_定额材料映射.csv",
    "T3_房建_标准物料库.csv",
    "T3_安装_标准物料库.csv",
    "N1_国家规范索引.csv",
    "N3_材料技术参数定义.csv",
    "N5_材料规范映射.csv",
    "N6_规范校验规则.csv",
    "项目默认值库.json",
    "材料名库.json",
    "已确认材料规则.json",
}

REQUIRED_EMBEDDED = {
    "test_ai_pipeline.py::PROCESS_KB",
    "test_ai_pipeline.py::MATERIAL_MAPPING_KB",
    "test_ai_pipeline.py::MATERIAL_CLASSIFICATION_KB",
    "test_ai_pipeline.py::LOSS_RATE_KB",
    "test_ai_pipeline.py::FORBIDDEN_OUTPUTS",
}


def main() -> int:
    if not MANIFEST_PATH.exists():
        print(f"FAIL 本地知识库包 missing manifest: {MANIFEST_PATH}")
        return 1
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    entries = manifest.get("entries", []) or []
    errors: list[str] = []

    for entry in entries:
        local_path = ROOT / entry.get("local_rel_path", "")
        if not local_path.exists():
            errors.append(f"missing local json: {entry.get('local_rel_path')}")
            continue
        if local_path.suffix != ".json":
            errors.append(f"non-json local file: {entry.get('local_rel_path')}")

    basenames = {Path(e.get("source_rel_path", "").split("::")[0]).name for e in entries}
    for basename in sorted(REQUIRED_RUNTIME_BASENAMES):
        if basename not in basenames:
            errors.append(f"missing runtime source basename: {basename}")

    embedded = {e.get("source_rel_path") for e in entries if e.get("category") == "runtime_embedded"}
    for item in sorted(REQUIRED_EMBEDDED):
        if item not in embedded:
            errors.append(f"missing embedded runtime kb: {item}")

    category_counts = Counter(e.get("category") for e in entries)
    total_rows = sum(e.get("row_count", 0) for e in entries if isinstance(e.get("row_count"), int))
    print("PASS" if not errors else "FAIL", "本地知识库包")
    print(f"entries: {len(entries)}")
    print(f"runtime_used: {sum(1 for e in entries if e.get('runtime_used'))}")
    print(f"rows_or_items: {total_rows}")
    print(f"categories: {dict(sorted(category_counts.items()))}")
    print(f"errors: {len(errors)}")
    for err in errors[:80]:
        print("ERROR", err)
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
