#!/usr/bin/env python3
"""Run deterministic golden cases for the BOQ -> procurement pipeline."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

THERMAL_WALL_XPS = """011001003002\t保温隔热墙面\t\t"1.保温隔热材料品种:B1级挤塑板
2.保温隔热材料厚度:50mm
3.保温隔热部位:打叶线门窗口四周
4.做法:
1)专用粘接剂粘贴50mm厚挤塑板保温层
2)6厚聚合物砂浆(压入两层耐碱玻纤网格布)
5.其他:详见设计图纸及技术要求，未详尽之处，以现行的国家规范及行业标准为准"\tm2\t87.36\t
END
"""

WINDOWSILL_CONCRETE = """010507005003\t"扶手、压顶
1.名称:混凝土窗台板
2.断面尺寸:340*100
3.混凝土种类:商砼
4.混凝土强度等级:C20
5.其他:详见设计图纸及技术要求，未详尽之处，以现行的国家规范及行业标准为准"\t\tm3\t1.96\t
END
"""


def run_pipeline(input_text: str) -> str:
    env = os.environ.copy()
    env["RUN_META_STRATEGY"] = "0"
    env["RUN_AI_MATERIALS"] = "0"
    proc = subprocess.run(
        [str(ROOT / ".venv/bin/python"), str(ROOT / "流水线测试.py")],
        input=input_text,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        cwd=ROOT,
        env=env,
        timeout=60,
        check=False,
    )
    if proc.returncode != 0:
        print(proc.stdout)
        raise AssertionError(f"pipeline exited with {proc.returncode}")
    return proc.stdout


def assert_contains(output: str, text: str):
    if text not in output:
        raise AssertionError(f"missing expected text: {text}")


def assert_not_contains(output: str, text: str):
    if text in output:
        raise AssertionError(f"unexpected text: {text}")


def main() -> int:
    output = run_pipeline(THERMAL_WALL_XPS)
    assert_contains(output, "项目默认推荐库: 命中体系1个")
    assert_contains(output, "材料数: 5")
    assert_contains(output, "绝热用挤塑聚苯乙烯泡沫塑料")
    assert_contains(output, "采购4.455 m³")
    assert_contains(output, "专用粘接剂")
    assert_contains(output, "449.904 kg")
    assert_contains(output, "耐碱玻纤网格布")
    assert_contains(output, "201.802 m²")
    assert_contains(output, "流程完成，存在待复核项")
    assert_not_contains(output, "壁纸")
    assert_not_contains(output, "织锦缎")
    assert_not_contains(output, "轻钢龙骨")
    assert_not_contains(output, "严重准确性问题")
    print("PASS golden: thermal_wall_xps_50mm")

    output = run_pipeline(WINDOWSILL_CONCRETE)
    assert_contains(output, "项目默认推荐库: 命中体系1个")
    assert_contains(output, "模板制作与安装")
    assert_contains(output, "钢筋网片或骨架加工绑扎")
    assert_contains(output, "材料数: 6")
    assert_contains(output, "预拌混凝土")
    assert_contains(output, "1.98 m³")
    assert_contains(output, "热轧光圆钢筋")
    assert_contains(output, "0.07 t")
    assert_contains(output, "木胶合板模板")
    assert_contains(output, "4.155 张")
    assert_not_contains(output, "材料清单为空")
    print("PASS golden: windowsill_concrete_secondary_structure")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except AssertionError as exc:
        print(f"FAIL golden: {exc}")
        raise SystemExit(1)
