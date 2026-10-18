"""Optional acadrust fallback. Keep each attempt and never merge converter outputs."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

import ezdxf

from .cad_ledger import classify_stderr, is_valid_dxf
from .config import pipeline as _pipeline

CONVERT_TIMEOUT_S = int(_pipeline()["cad"]["convert_timeout_s"])


def find_acadrust_tool() -> str | None:
    configured = os.environ.get("EXTRACTOR_ACADRUST_BIN")
    repo_root = Path(__file__).resolve().parents[2]
    bundled = repo_root / "integrations/acadrust/target/release/extractor-dwg"
    candidates = (
        configured,
        str(bundled),
        str(repo_root / "bin/extractor-dwg"),
        "/tmp/bin/extractor-dwg",
        "/tmp/extractor-dwg",
        shutil.which("extractor-dwg"),
    )
    for candidate in candidates:
        if candidate and os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    return None


def validate_document(path: str) -> tuple[bool, str]:
    valid, reason = is_valid_dxf(path)
    if not valid:
        return valid, reason
    try:
        doc = ezdxf.readfile(path)
        if not any(len(space) for space in doc.layouts):
            return False, "DXF 没有可读空间实体"
    except Exception as exc:
        return False, f"DXF 对象解析失败：{type(exc).__name__}: {exc}"
    return True, ""


def convert_acadrust(source: str, target: str) -> dict:
    tool = find_acadrust_tool()
    result = {"converter": "acadrust", "tool": "extractor-dwg", "exit_code": None,
              "accepted": False, "output_nonempty": False, "dxf_valid": False,
              "warning_lines": 0, "error_lines": 0, "log_missing": False,
              "stderr_excerpt": "", "note": "acadrust 未安装，保留现有降级流程"}
    if not tool:
        return result
    parent = os.path.dirname(os.path.abspath(target))
    with tempfile.TemporaryDirectory(prefix="acadrust-", dir=parent) as work:
        output = os.path.join(work, "drawing.dxf")
        report = os.path.join(work, "read.json")
        try:
            proc = subprocess.run([tool, source, output, report], capture_output=True,
                                  text=True, errors="replace", timeout=CONVERT_TIMEOUT_S, check=False)
            warnings, errors, excerpt = classify_stderr(proc.stderr)
            stats = {}
            if os.path.isfile(report):
                with open(report, encoding="utf-8") as stream:
                    stats = json.load(stream)
            valid, reason = validate_document(output)
            complete = stats.get("stream_completed") is True
            accepted = proc.returncode == 0 and valid and complete
            result.update(exit_code=proc.returncode, accepted=accepted, dxf_valid=valid,
                          output_nonempty=os.path.isfile(output) and os.path.getsize(output) > 0,
                          warning_lines=warnings + len(stats.get("diagnostics") or [])
                          + int(stats.get("skipped_source_records") or 0)
                          + int(stats.get("recovered_errors") or 0),
                          error_lines=errors, stderr_excerpt=excerpt, reader_stats=stats,
                          note=("acadrust 兜底转换通过；尚未证明原图对象完整保留" if accepted
                                else reason or "acadrust 转换失败或读取未完成"))
            if accepted:
                os.replace(output, target)
        except (OSError, subprocess.TimeoutExpired, ValueError) as exc:
            result.update(error_lines=1, note=f"acadrust 未完成：{type(exc).__name__}: {exc}")
    return result
