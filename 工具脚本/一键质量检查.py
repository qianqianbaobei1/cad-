#!/usr/bin/env python3
"""一键运行所有质量检查，生成综合报告。P1/P2完成后使用。"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REPORT_PATH = ROOT / "过程数据" / "质量检查综合报告.json"

CHECKS = [
    {
        "name": "项目默认值库(增强)",
        "script": "检查项目默认值_增强.py",
        "weight": 30,
    },
    {
        "name": "T3标准物料库",
        "script": "检查T3物料库.py",
        "weight": 30,
    },
    {
        "name": "数据一致性校验",
        "script": "校验数据一致性.py",
        "weight": 20,
    },
    {
        "name": "公式合理性",
        "script": "校验公式合理性.py",
        "weight": 20,
    },
]


def run_check(script_name: str) -> dict:
    """运行单个检查脚本并解析输出"""
    script_path = ROOT / "工具脚本" / script_name
    if not script_path.exists():
        return {"status": "missing", "error": f"脚本不存在: {script_path}"}

    try:
        result = subprocess.run(
            [sys.executable, str(script_path)],
            capture_output=True, text=True, timeout=60,
            cwd=str(ROOT)
        )
        output = result.stdout
        passed = "PASS" in output.split("\n")[0] if output else False

        # 提取关键指标
        metrics = {}
        for line in output.split("\n"):
            if ":" in line and any(k in line for k in ["ERRORS", "WARNINGS", "PASS", "FAIL",
                                                        "覆盖", "可执行", "总数"]):
                parts = line.split(":", 1)
                if len(parts) == 2:
                    key = parts[0].strip()
                    val = parts[1].strip()
                    metrics[key] = val

        return {
            "status": "pass" if result.returncode == 0 else "fail",
            "passed": passed,
            "returncode": result.returncode,
            "output": output,
            "metrics": metrics,
        }
    except subprocess.TimeoutExpired:
        return {"status": "timeout", "error": "检查超时(60s)"}
    except Exception as e:
        return {"status": "error", "error": str(e)}


def main():
    print("=" * 60)
    print("知识库数据质量综合检查")
    print(f"时间: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    results = {}
    total_score = 0
    max_score = 0

    for check in CHECKS:
        name = check["name"]
        weight = check["weight"]
        script = check["script"]

        print(f"\n── {name} ──")
        result = run_check(script)
        results[name] = result

        status = result.get("status", "unknown")
        if status == "pass":
            score = weight
            print(f"  ✅ PASS")
        elif status == "fail":
            score = 0
            print(f"  ❌ FAIL (rc={result.get('returncode')})")
        else:
            score = 0
            print(f"  ⚠️  {status}: {result.get('error', '')}")

        total_score += score
        max_score += weight

        # 显示关键指标
        metrics = result.get("metrics", {})
        for k, v in metrics.items():
            if any(kw in k for kw in ["ERRORS", "WARNINGS", "覆盖", "可执行", "总数", "通过"]):
                print(f"     {k}: {v}")

    # 综合评分
    overall_pct = total_score / max_score * 100 if max_score > 0 else 0

    print(f"\n{'=' * 60}")
    print(f"综合评分: {total_score}/{max_score} ({overall_pct:.0f}%)")
    if overall_pct >= 90:
        print("等级: A — 数据质量优秀，可以进入生产")
    elif overall_pct >= 70:
        print("等级: B — 数据质量良好，部分项目需人工关注")
    elif overall_pct >= 50:
        print("等级: C — 数据质量问题较多，建议修复后再使用")
    else:
        print("等级: D — 数据质量严重不足，必须修复")

    # 保存报告
    report = {
        "timestamp": time.time(),
        "overall_score": overall_pct,
        "total_score": total_score,
        "max_score": max_score,
        "results": {name: {"status": r["status"], "metrics": r.get("metrics", {}), "returncode": r.get("returncode")}
                     for name, r in results.items()},
    }
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(REPORT_PATH, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(f"\n报告已保存: {REPORT_PATH}")

    return 0 if overall_pct >= 70 else 1


if __name__ == "__main__":
    raise SystemExit(main())
