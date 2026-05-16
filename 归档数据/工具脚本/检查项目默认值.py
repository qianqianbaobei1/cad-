#!/usr/bin/env python3
"""Validate 项目默认值库 executable structure.

The check is intentionally deterministic: it verifies that recommendation rows
can be bound to systems, have quantity rules, have loss rates, and use formula
families supported by the local Stage3 executor.
"""
from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
KB_PATH = ROOT / "项目数据" / "项目默认值库.json"

SUPPORTED_FORMULA_MARKERS = [
    "BOQ_AREA",
    "BOQ_QTY",
    "BOQ_VOLUME",
    "BOQ_LENGTH",
    "BOQ_COUNT",
    "STEEL_QTY",
    "STEEL_TON",
    "STEEL_AREA",
    "WIDTH",
    "FLOOR_COUNT",
    "CRACK_LENGTH",
    "DUCT_LENGTH",
    "FRAME_LENGTH",
    "GLASS_PERIMETER",
]

RUNTIME_PARAMS = {
    "loss_rate",
    "thickness_mm",
    "thickness",
    "density",
    "coeff",
    "coverage_rate",
    "coverage",
    "coats",
    "usage_per_m",
    "usage_per_m2",
    "usage_per_ton",
    "turnover_times",
    "turnover",
    "steel_quantity",
    "ratio",
    "blocks_per_m3",
    "joint_depth",
    "joint_width",
    "crack_length",
    "cross_section",
    "density_per_m2",
    "strand_count",
    "unit_weight",
    "steel_area",
    "dft",
    "solids",
    "rebound_rate",
    "rebound_coeff",
}

RUNTIME_ONLY_PARAMS = {
    "steel_quantity",
    "steel_area",
    "crack_length",
    "duct_length",
    "frame_length",
    "glass_perimeter",
    "strand_count",
}


def has_default_for_param(rule: dict, rec: dict, param: str) -> bool:
    defaults = rec.get("default_spec_values") or {}
    aliases = {
        "loss_rate": ["loss_rate_hint"],
        "turnover_times": ["default_turnover"],
        "coverage_rate": ["default_coverage"],
        "rebound_rate": ["default_rebound_coeff"],
        "blocks_per_m3": ["default_blocks_per_m3"],
        "cross_section": ["default_cross_section"],
    }
    candidates = [
        f"default_{param}",
        param,
        *aliases.get(param, []),
    ]
    return any(k in rule for k in candidates) or any(k in defaults for k in candidates) or any(k in rec for k in candidates)


def main() -> int:
    kb = json.loads(KB_PATH.read_text(encoding="utf-8"))
    systems = {s.get("system_id") for s in kb.get("default_systems", []) or []}
    recs = kb.get("material_recommendations", []) or []
    refs = {
        r.get("ref_id") or r.get("standard_ref_id") or r.get("id") or r.get("code")
        for r in kb.get("standard_refs", []) or []
    }

    errors: list[str] = []
    warnings: list[str] = []
    formula_counter = Counter()

    seen_rec_ids = set()
    for idx, rec in enumerate(recs, start=1):
        rec_id = rec.get("rec_id") or f"row-{idx}"
        if rec_id in seen_rec_ids:
            errors.append(f"{rec_id}: duplicate rec_id")
        seen_rec_ids.add(rec_id)

        if not rec.get("material_name"):
            errors.append(f"{rec_id}: missing material_name")
        if rec.get("system_id") not in systems:
            errors.append(f"{rec_id}: broken system_id {rec.get('system_id')}")
        if rec.get("loss_rate_hint") is None:
            errors.append(f"{rec_id}: missing loss_rate_hint")
        if not rec.get("basis"):
            errors.append(f"{rec_id}: missing basis")
        if not isinstance(rec.get("default_spec_values"), dict) or not rec.get("default_spec_values"):
            errors.append(f"{rec_id}: missing default_spec_values")
        if rec.get("required_params") is None:
            errors.append(f"{rec_id}: required_params must be list, not null")

        rule = rec.get("quantity_rule")
        if not isinstance(rule, dict) or not rule.get("formula"):
            errors.append(f"{rec_id}: missing quantity_rule.formula")
            continue
        formula = str(rule.get("formula"))
        formula_counter[formula] += 1
        if not any(marker in formula.upper() for marker in SUPPORTED_FORMULA_MARKERS):
            errors.append(f"{rec_id}: unsupported formula family {formula}")

        if "THICKNESS(m) / 1000" in formula:
            errors.append(f"{rec_id}: ambiguous thickness unit, use THICKNESS_MM / 1000")

        depends = rule.get("depends_on") or []
        if not isinstance(depends, list):
            errors.append(f"{rec_id}: depends_on must be list")
            depends = []
        for param in depends:
            if param not in RUNTIME_PARAMS:
                warnings.append(f"{rec_id}: unknown depends_on param {param}")
            if param in RUNTIME_ONLY_PARAMS:
                continue
            if param != "loss_rate" and not has_default_for_param(rule, rec, param):
                # Some params are design inputs that may be extracted from BOQ, so warn, not fail.
                warnings.append(f"{rec_id}: depends_on {param} has no default value")

        for ref in rec.get("standard_ref_ids", []) or []:
            if refs and ref not in refs:
                errors.append(f"{rec_id}: broken standard_ref_id {ref}")

    print("PASS" if not errors else "FAIL", "项目默认值库")
    print(f"systems: {len(systems)}")
    print(f"materials: {len(recs)}")
    print(f"formula_families: {len(formula_counter)}")
    print(f"broken_refs/errors: {len(errors)}")
    print(f"warnings: {len(warnings)}")
    if errors:
        for err in errors[:80]:
            print("ERROR", err)
    if warnings:
        for warn in warnings[:40]:
            print("WARN", warn)
        if len(warnings) > 40:
            print(f"WARN ... {len(warnings) - 40} more")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
