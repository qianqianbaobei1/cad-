#!/usr/bin/env python3
"""从外部 ../知识库/ 拉取最新数据到 标准知识库/源数据/，并重建索引。

注意：项目已自带全部知识库数据（标准知识库/源数据/），外部知识库为可选项。
此脚本仅在外部知识库存在且有更新时使用。
"""
from __future__ import annotations

import argparse
import filecmp
import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
外部知识库 = (ROOT / ".." / "知识库").resolve()
源数据目录 = ROOT / "标准知识库" / "源数据"
构建脚本 = ROOT / "工具脚本" / "构建索引.py"
PYTHON = sys.executable

# 需要同步的核心文件映射：外部路径 → 源数据内相对路径
同步清单 = {
    "01_定额库/Q1_定额索引.csv":              "01_定额库/Q1_定额索引.csv",
    "01_定额库/Q2_定额材料消耗.csv":          "01_定额库/Q2_定额材料消耗.csv",
    "01_定额库/Q3_定额材料映射.csv":          "01_定额库/Q3_定额材料映射.csv",
    "01_定额库/Q0_清单项目编码_房建工程.csv":  "01_定额库/Q0_清单项目编码_房建工程.csv",
    "01_定额库/Q0_清单项目编码_安装工程.csv":  "01_定额库/Q0_清单项目编码_安装工程.csv",
    "01_定额库/T2_清单材料映射库.csv":         "01_定额库/T2_清单材料映射库.csv",
    "03_国家规范库/N1_国家规范索引.csv":       "03_国家规范库/N1_国家规范索引.csv",
    "03_国家规范库/N3_材料技术参数定义.csv":   "03_国家规范库/N3_材料技术参数定义.csv",
    "03_国家规范库/N5_材料规范映射.csv":       "03_国家规范库/N5_材料规范映射.csv",
    "03_国家规范库/N6_规范校验规则.csv":       "03_国家规范库/N6_规范校验规则.csv",
    "06_品类树/T3_品类映射.csv":              "06_品类树/T3_品类映射.csv",
    "06_品类树/品类节点索引.csv":              "06_品类树/品类节点索引.csv",
    "01_房屋建筑与装饰工程/CSV导出/01_t1_分部代码路由.csv": "01_房屋建筑与装饰工程/CSV导出/01_t1_分部代码路由.csv",
    "01_房屋建筑与装饰工程/CSV导出/02_t2_清单材料映射.csv": "01_房屋建筑与装饰工程/CSV导出/02_t2_清单材料映射.csv",
    "01_房屋建筑与装饰工程/CSV导出/03_t3_标准物料库.csv":   "01_房屋建筑与装饰工程/CSV导出/03_t3_标准物料库.csv",
    "02_通用安装工程/CSV导出/01_t1_分部代码路由.csv": "02_通用安装工程/CSV导出/01_t1_分部代码路由.csv",
    "02_通用安装工程/CSV导出/02_t3_标准物料库.csv":   "02_通用安装工程/CSV导出/02_t3_标准物料库.csv",
    "02_通用安装工程/CSV导出/03_t2_清单材料映射.csv": "02_通用安装工程/CSV导出/03_t2_清单材料映射.csv",
}


def 同步(重建索引: bool = True) -> dict:
    if not 外部知识库.exists():
        print(f"外部知识库不存在: {外部知识库}")
        print("项目已自带全部知识库数据，无需同步。跳过。")
        return {"新增": [], "更新": [], "未变": [], "缺失外部源": list(同步清单.keys())}

    结果 = {"新增": [], "更新": [], "未变": [], "缺失外部源": []}

    for 外部路径, 本地路径 in 同步清单.items():
        src = 外部知识库 / 外部路径
        dst = 源数据目录 / 本地路径

        if not src.exists():
            结果["缺失外部源"].append(外部路径)
            print(f"  ✗ 外部缺失: {外部路径}")
            continue

        dst.parent.mkdir(parents=True, exist_ok=True)

        if dst.exists() and filecmp.cmp(str(src), str(dst), shallow=False):
            结果["未变"].append(外部路径)
        else:
            shutil.copy2(str(src), str(dst))
            if dst.exists():
                结果["更新"].append(外部路径)
                print(f"  ✓ 更新: {外部路径}  ({src.stat().st_size:,} bytes)")
            else:
                结果["新增"].append(外部路径)
                print(f"  + 新增: {外部路径}  ({src.stat().st_size:,} bytes)")

    # 汇总
    print(f"\n同步结果: 新增={len(结果['新增'])}, 更新={len(结果['更新'])}, "
          f"未变={len(结果['未变'])}, 缺失={len(结果['缺失外部源'])}")

    if 重建索引 and (结果["新增"] or 结果["更新"]):
        print("\n→ 重建本地索引...")
        subprocess.run([PYTHON, str(构建脚本)], cwd=str(ROOT), check=True)

    return 结果


def main() -> int:
    parser = argparse.ArgumentParser(description="从外部知识库同步最新数据到标准知识库/源数据（外部KB可选）")
    parser.add_argument("--不重建索引", action="store_true", help="只同步文件，不重建索引")
    parser.add_argument("--预览", action="store_true", help="只检查差异，不实际复制")
    args = parser.parse_args()

    if args.预览:
        print(f"外部知识库: {外部知识库} {'(存在)' if 外部知识库.exists() else '(不存在 — 项目已自带全部数据)'}")
        print(f"本地源数据: {源数据目录}\n")
        for 外部路径, 本地路径 in 同步清单.items():
            src = 外部知识库 / 外部路径
            dst = 源数据目录 / 本地路径
            if not src.exists():
                print(f"  ✗ 外部缺失: {外部路径}")
            elif not dst.exists():
                print(f"  + 需新增: {外部路径} ({src.stat().st_size:,} bytes)")
            elif not filecmp.cmp(str(src), str(dst), shallow=False):
                print(f"  ~ 需更新: {外部路径} ({src.stat().st_size:,} bytes)")
            else:
                print(f"  = 未变:   {外部路径}")
        return 0

    同步(重建索引=not args.不重建索引)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
