#!/usr/bin/env python3
"""
回归测试套件 — 验证流水线规则变更不影响历史场景。

用法:
  python3 工具脚本/regression_test.py              # 运行所有测试
  python3 工具脚本/regression_test.py --case TC-001  # 运行单个案例
  python3 工具脚本/regression_test.py --update        # 更新期望输出
  python3 工具脚本/regression_test.py --list          # 列出所有案例
"""
import json
import sys
from pathlib import Path
from dataclasses import dataclass, field

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "服务端"))

CASES_DIR = ROOT / "项目数据" / "regression_cases"
EXPECTED_DIR = ROOT / "项目数据" / "regression_expected"


# ══════════════════════════════════════
# 数据模型
# ══════════════════════════════════════

@dataclass
class TestCase:
    case_id: str
    name: str
    description: str
    boq_input: str
    province: str
    expected: dict
    check_fields: list[str]
    tolerance: dict = field(default_factory=dict)


@dataclass
class TestResult:
    case_id: str
    name: str
    passed: bool
    checks: list[dict]  # [{"field": "...", "expected": ..., "actual": ..., "passed": bool}]
    error: str = ""


# ══════════════════════════════════════
# 案例加载
# ══════════════════════════════════════

def load_test_cases() -> list[TestCase]:
    """从 regression_cases 目录加载所有测试案例"""
    CASES_DIR.mkdir(parents=True, exist_ok=True)
    EXPECTED_DIR.mkdir(parents=True, exist_ok=True)

    cases = []
    for f in sorted(CASES_DIR.glob("*.json")):
        with open(f, "r", encoding="utf-8") as fp:
            data = json.load(fp)
        cases.append(TestCase(
            case_id=data.get("case_id", f.stem),
            name=data.get("name", f.stem),
            description=data.get("description", ""),
            boq_input=data.get("boq_input", ""),
            province=data.get("province", "SN"),
            expected=data.get("expected_output", {}),
            check_fields=data.get("check_fields", []),
            tolerance=data.get("tolerance", {}),
        ))
    return cases


# ══════════════════════════════════════
# 执行测试（不调用AI，仅验证KB模块）
# ══════════════════════════════════════

def _get_actual_output(case: TestCase) -> dict:
    """运行测试案例的 KB 模块，返回实际输出（不调用AI）"""
    from boq_precheck import scan_boq_content
    from material_decision_engine import (
        apply_material_decisions, decision_to_pipeline_format,
    )
    from 流水线执行 import _load_process_chains, parse_input

    # 解析输入
    try:
        data = parse_input(case.boq_input)
        boq = data["工程量清单"]
    except Exception:
        boq = {}

    boq_code = boq.get("项目编码", "")
    boq_name = boq.get("项目名称", "")
    feature_text = boq.get("项目特征", {}).get("原文描述", case.boq_input)
    features = {k: v for k, v in boq.get("项目特征", {}).items() if k != "原文描述"}

    # Pre-check
    pre_check = scan_boq_content(case.boq_input)

    # Process chains
    chains = _load_process_chains()
    standard_code = boq_code[:9] if len(boq_code) >= 9 else boq_code[:6]
    chain = chains.get(standard_code, [])

    # Decision engine
    try:
        dresult = apply_material_decisions(boq_code, boq_name, feature_text, features)
        materials = decision_to_pipeline_format(dresult)
    except Exception:
        dresult = None
        materials = []

    return {
        "process_count": len(chain),
        "process_names": [s["process_name"] for s in chain],
        "pre_check_quality": pre_check.overall_quality,
        "pre_check_score": pre_check.quality_score,
        "has_external_ref": pre_check.has_external_ref,
        "material_count": len(materials),
        "material_names": [m["material_name"] for m in materials],
        "decision_rules_matched": dresult.total_rules_matched if dresult else 0,
        "required_materials": [m.material_name for m in (dresult.required_materials if dresult else [])],
        "aux_s1_materials": [m.material_name for m in (dresult.aux_s1_materials if dresult else [])],
        "aux_s2_materials": [m.material_name for m in (dresult.aux_s2_materials if dresult else [])],
        "aux_s3_materials": [m.material_name for m in (dresult.aux_s3_materials if dresult else [])],
        "forbidden_materials": [m.material_name for m in (dresult.forbidden_materials if dresult else [])],
        "excluded_materials": [m.material_name for m in (dresult.excluded_materials if dresult else [])],
        "missing_required": dresult.missing_required if dresult else [],
        "warnings": dresult.warnings if dresult else [],
    }


def _check_value(expected: object, actual: object, field: str, tolerance: dict) -> dict:
    """比较期望值和实际值"""
    if isinstance(expected, list):
        # 列表比较：忽略顺序
        passed = set(expected) == set(actual)
        return {
            "field": field,
            "expected": expected,
            "actual": actual,
            "passed": passed,
            "detail": f"期望 {len(expected)} 项，实际 {len(actual)} 项"
            if not passed else "",
        }
    elif isinstance(expected, (int, float)):
        tol = tolerance.get(field, 0.001)
        passed = abs(float(expected) - float(actual)) <= tol
        return {
            "field": field,
            "expected": expected,
            "actual": actual,
            "passed": passed,
            "detail": f"差异 {abs(float(expected) - float(actual)):.4f}"
            if not passed else "",
        }
    else:
        passed = expected == actual
        return {
            "field": field,
            "expected": expected,
            "actual": actual,
            "passed": passed,
            "detail": "",
        }


def run_test_case(case: TestCase) -> TestResult:
    """运行单个测试案例"""
    try:
        actual = _get_actual_output(case)
    except Exception as e:
        return TestResult(
            case_id=case.case_id,
            name=case.name,
            passed=False,
            checks=[],
            error=f"执行异常: {e}",
        )

    checks = []
    for field in case.check_fields:
        if field in case.expected:
            check = _check_value(case.expected[field], actual.get(field), field, case.tolerance)
            checks.append(check)

    # 如果有 "no_external_ref" 期望
    if "no_external_refs" in case.expected and case.expected["no_external_refs"]:
        checks.append({
            "field": "external_refs",
            "expected": "无外部依赖",
            "actual": "有外部依赖" if actual.get("has_external_ref") else "无外部依赖",
            "passed": not actual.get("has_external_ref"),
            "detail": "",
        })

    all_passed = all(c.get("passed", False) for c in checks) if checks else True

    return TestResult(
        case_id=case.case_id,
        name=case.name,
        passed=all_passed,
        checks=checks,
    )


# ══════════════════════════════════════
# 主入口
# ══════════════════════════════════════

def run_all_tests() -> list[TestResult]:
    """运行所有测试案例"""
    cases = load_test_cases()
    return [run_test_case(c) for c in cases]


def update_expected(case_id: str | None = None):
    """更新期望输出"""
    cases = load_test_cases()
    if case_id:
        cases = [c for c in cases if c.case_id == case_id]

    for case in cases:
        actual = _get_actual_output(case)
        expected_file = EXPECTED_DIR / f"{case.case_id}_expected.json"
        with open(expected_file, "w", encoding="utf-8") as f:
            json.dump(actual, f, ensure_ascii=False, indent=2)
        print(f"Updated: {expected_file}")


def print_results(results: list[TestResult]):
    """打印测试结果"""
    passed = sum(1 for r in results if r.passed)
    failed = sum(1 for r in results if not r.passed and not r.error)
    errors = sum(1 for r in results if r.error)

    print(f"\n{'='*60}")
    print(f"回归测试结果: {len(results)} 案例")
    print(f"  ✓ 通过: {passed}")
    if failed:
        print(f"  ✗ 失败: {failed}")
    if errors:
        print(f"  ⚠ 错误: {errors}")
    print(f"{'='*60}")

    for r in results:
        status = "✓" if r.passed else ("✗" if not r.error else "⚠")
        print(f"\n{status} [{r.case_id}] {r.name}")
        if r.error:
            print(f"  Error: {r.error}")
        for c in r.checks:
            c_status = "✓" if c["passed"] else "✗"
            print(f"  {c_status} {c['field']}: expected={c['expected']}, actual={c['actual']}")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="BOQ Pipeline Regression Test Suite")
    parser.add_argument("--case", help="Run specific case by ID")
    parser.add_argument("--list", action="store_true", help="List all test cases")
    parser.add_argument("--update", action="store_true", help="Update expected outputs")
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose output")
    args = parser.parse_args()

    if args.list:
        cases = load_test_cases()
        for c in cases:
            print(f"  [{c.case_id}] {c.name}")
            print(f"    {c.description}")
            print(f"    Checks: {c.check_fields}")
        sys.exit(0)

    if args.update:
        update_expected(args.case)
        sys.exit(0)

    if args.case:
        cases = load_test_cases()
        target = next((c for c in cases if c.case_id == args.case), None)
        if not target:
            print(f"Case not found: {args.case}")
            sys.exit(1)
        result = run_test_case(target)
        print_results([result])
    else:
        results = run_all_tests()
        print_results(results)

    # Exit code
    all_pass = all(r.passed for r in results)
    sys.exit(0 if all_pass else 1)
