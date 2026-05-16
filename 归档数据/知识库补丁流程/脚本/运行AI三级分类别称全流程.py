#!/usr/bin/env python3
"""运行 AI 三级分类别称候选、核验、回填、重建材料库全流程。"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


脚本目录 = Path(__file__).resolve().parent
项目目录 = 脚本目录.parents[1]
PYTHON = sys.executable


def run(cmd: list[str]) -> None:
    print("\n> " + " ".join(cmd), flush=True)
    subprocess.run(cmd, cwd=str(项目目录), check=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="运行 AI 三级分类别称全流程")
    parser.add_argument("--分类ID", nargs="*", default=[])
    parser.add_argument("--分类名称", default="")
    parser.add_argument("--全部", action="store_true")
    parser.add_argument("--限制", type=int, default=0)
    parser.add_argument("--每批材料数", type=int, default=25)
    parser.add_argument("--请求间隔秒", type=float, default=1.0)
    parser.add_argument("--覆盖", action="store_true")
    args = parser.parse_args()

    run([PYTHON, str(脚本目录 / "生成三级分类材料库.py")])

    gen_cmd = [
        PYTHON, str(脚本目录 / "AI生成三级分类别称候选.py"),
        "--每批材料数", str(args.每批材料数),
        "--请求间隔秒", str(args.请求间隔秒),
    ]
    if args.全部 or (not args.分类ID and not args.分类名称):
        gen_cmd.append("--全部")
    if args.分类ID:
        gen_cmd.append("--分类ID")
        gen_cmd.extend(args.分类ID)
    if args.分类名称:
        gen_cmd.extend(["--分类名称", args.分类名称])
    if args.限制:
        gen_cmd.extend(["--限制", str(args.限制)])
    if args.覆盖:
        gen_cmd.append("--覆盖")
    run(gen_cmd)

    verify_cmd = [
        PYTHON, str(脚本目录 / "AI核验三级分类别称候选.py"),
        "--请求间隔秒", str(args.请求间隔秒),
    ]
    if args.覆盖:
        verify_cmd.append("--覆盖")
    run(verify_cmd)

    run([PYTHON, str(脚本目录 / "回填AI已核验别称.py")])
    run([PYTHON, str(脚本目录 / "生成三级分类材料库.py")])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
