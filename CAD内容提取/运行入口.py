#!/usr/bin/env python3
"""
统一运行入口。

默认流程：
    已有 BOQ + 已有 CAD 做法表提取结果
    -> 项目知识库
    -> 材料拆解工作区
    -> 采购计划物料清单

用法：
    python3 运行入口.py
    python3 运行入口.py --project 宿州302
    python3 运行入口.py --stage status
    python3 运行入口.py --stage knowledge
    python3 运行入口.py --stage workspace
    python3 运行入口.py --stage cad --skip-llm
    python3 运行入口.py --stage all
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any


SCRIPT_DIR = Path(__file__).parent
PROJECTS_DIR = SCRIPT_DIR / "projects"

STAGE_CONFIG = {
    "cad": {
        "script": "批量管线.py",
        "description": "从 DWG 抽取做法表。耗时较长，依赖 dwgread 和可选 LLM API。",
    },
    "knowledge": {
        "script": "构建项目知识库.py",
        "description": "把 BOQ 和图纸做法表整理成项目知识库。",
    },
    "workspace": {
        "script": "构建材料拆解工作区.py",
        "description": "把项目知识库转换成材料拆解任务、缺失资料清单和人工确认 Excel。",
    },
    "procurement": {
        "script": "生成采购计划物料清单.py",
        "description": "生成最终交付的单一采购计划物料清单。",
    },
}


def read_json(path: Path) -> Any:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")


def project_root(project: str) -> Path:
    root = PROJECTS_DIR / project
    if not root.exists():
        raise FileNotFoundError(f"项目不存在: {root}")
    return root


def count_files(path: Path, pattern: str) -> int:
    if not path.exists():
        return 0
    return len(list(path.glob(pattern)))


def load_manifest(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        return read_json(path)
    except json.JSONDecodeError:
        return None


def inspect_project(project: str) -> dict[str, Any]:
    root = project_root(project)
    input_dir = root / "原始输入"
    extract_dir = root / "提取结果" / "做法表"
    kb_dir = root / "项目知识库"
    workspace_dir = root / "材料拆解工作区"
    final_output_dir = root / "最终输出"

    return {
        "project": project,
        "project_root": str(root),
        "inputs": {
            "boq_excel_count": count_files(input_dir, "*.xlsx"),
            "boq_dir": str(input_dir),
            "practice_json_count": count_files(extract_dir, "做法表_*.json"),
            "practice_dir": str(extract_dir),
        },
        "knowledge_base": {
            "exists": kb_dir.exists(),
            "dir": str(kb_dir),
            "manifest": load_manifest(kb_dir / "manifest.json"),
        },
        "workspace": {
            "exists": workspace_dir.exists(),
            "dir": str(workspace_dir),
            "manifest": load_manifest(workspace_dir / "manifest.json"),
            "review_workbook_exists": (workspace_dir / "材料拆解工作区_人工确认.xlsx").exists(),
        },
        "final_output": {
            "exists": (final_output_dir / "采购计划物料清单.xlsx").exists(),
            "file": str(final_output_dir / "采购计划物料清单.xlsx"),
        },
    }


def print_status(status: dict[str, Any]) -> None:
    print(f"项目: {status['project']}")
    print(f"目录: {status['project_root']}")
    print("")
    print("输入资料:")
    print(f"  BOQ Excel: {status['inputs']['boq_excel_count']} 个")
    print(f"  CAD 做法表 JSON: {status['inputs']['practice_json_count']} 个")
    print("")

    kb_manifest = status["knowledge_base"]["manifest"]
    print("项目知识库:")
    if kb_manifest:
        print(f"  已生成: {status['knowledge_base']['dir']}")
        print(f"  统计: {kb_manifest.get('counts', {})}")
    else:
        print("  未生成")

    workspace_manifest = status["workspace"]["manifest"]
    print("")
    print("材料拆解工作区:")
    if workspace_manifest:
        print(f"  已生成: {status['workspace']['dir']}")
        print(f"  统计: {workspace_manifest.get('counts', {})}")
        print(f"  人工确认 Excel: {status['workspace']['review_workbook_exists']}")
    else:
        print("  未生成")

    print("")
    print("最终输出:")
    if status["final_output"]["exists"]:
        print(f"  {status['final_output']['file']}")
    else:
        print("  未生成")


def run_command(command: list[str]) -> None:
    print("")
    print("运行命令:", flush=True)
    print("  " + " ".join(command), flush=True)
    result = subprocess.run(command, cwd=SCRIPT_DIR)
    if result.returncode != 0:
        raise SystemExit(result.returncode)


def run_stage(project: str, stage: str, args: argparse.Namespace) -> None:
    config = STAGE_CONFIG[stage]
    script_path = SCRIPT_DIR / config["script"]
    if not script_path.exists():
        raise FileNotFoundError(f"阶段脚本不存在: {script_path}")

    print("")
    print(f"开始阶段: {stage}", flush=True)
    print(f"说明: {config['description']}", flush=True)

    command = [sys.executable, str(script_path), "--project", project]
    if stage == "cad":
        if args.skip_llm:
            command.append("--skip-llm")
        if args.dry_run:
            command.append("--dry-run")
        if args.building:
            command.extend(["--building", args.building])
    run_command(command)


def validate_before_stage(project: str, stage: str) -> None:
    status = inspect_project(project)
    if stage == "knowledge":
        if status["inputs"]["boq_excel_count"] == 0:
            raise SystemExit("缺少 BOQ Excel，请先放入 projects/<项目名>/原始输入/")
        if status["inputs"]["practice_json_count"] == 0:
            raise SystemExit("缺少 CAD 做法表 JSON。请先运行 --stage cad，或放入 projects/<项目名>/提取结果/做法表/")
    if stage == "workspace":
        if not status["knowledge_base"]["manifest"]:
            raise SystemExit("缺少项目知识库。请先运行 --stage knowledge")
    if stage == "procurement":
        if not status["workspace"]["manifest"]:
            raise SystemExit("缺少材料拆解工作区。请先运行 --stage workspace")


def write_run_report(project: str, stages: list[str]) -> None:
    root = project_root(project)
    status = inspect_project(project)
    report = {
        "project": project,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "executed_stages": stages,
        "status": status,
        "final_output": status["final_output"],
        "recommended_next_step": "对外只交付 最终输出/采购计划物料清单.xlsx；中间目录仅供系统内部追溯和迭代使用。",
    }
    write_json(root / "运行报告.json", report)
    print("")
    print(f"运行报告: {root / '运行报告.json'}")


def main() -> None:
    parser = argparse.ArgumentParser(description="装修材料清单系统统一运行入口")
    parser.add_argument("--project", default="宿州302", help="项目名称，默认宿州302")
    parser.add_argument(
        "--stage",
        choices=["status", "cad", "knowledge", "workspace", "procurement", "all"],
        default="all",
        help="运行阶段。默认 all：知识库 + 拆解工作区 + 采购计划物料清单，不默认跑 CAD。",
    )
    parser.add_argument("--skip-llm", action="store_true", help="运行 CAD 阶段时跳过 LLM 清洗")
    parser.add_argument("--dry-run", action="store_true", help="运行 CAD 阶段时只预览文件，不处理")
    parser.add_argument("--building", help="运行 CAD 阶段时只处理指定楼栋，例如 Y-1#")
    args = parser.parse_args()

    project_root(args.project)

    if args.stage == "status":
        print_status(inspect_project(args.project))
        return

    stages = ["knowledge", "workspace", "procurement"] if args.stage == "all" else [args.stage]
    executed = []
    for stage in stages:
        validate_before_stage(args.project, stage)
        run_stage(args.project, stage, args)
        executed.append(stage)

    write_run_report(args.project, executed)
    print("")
    print("当前状态:")
    print_status(inspect_project(args.project))


if __name__ == "__main__":
    main()
