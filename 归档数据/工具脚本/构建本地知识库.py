#!/usr/bin/env python3
"""Build a local JSON mirror for all knowledge bases used by the pipeline."""
from __future__ import annotations

import ast
import csv
import hashlib
import json
import time
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
源数据_DIR = ROOT / "标准知识库" / "源数据"
OUT_ROOT = ROOT / "项目数据" / "本地知识库包"

SOURCE_DIRS = [
    源数据_DIR / "01_定额库",
    源数据_DIR / "03_国家规范库",
    源数据_DIR / "06_品类树",
    源数据_DIR / "01_房屋建筑与装饰工程" / "CSV导出",
    源数据_DIR / "02_通用安装工程" / "CSV导出",
    源数据_DIR / "开发者导出",
    源数据_DIR / "省份适配" / "CSV导出",
    源数据_DIR / "04_采购拆解平台" / "房建BOQ材料拆解规则库",
    源数据_DIR / "05_品类树损耗率",
]

LOCAL_JSON_FILES = [
    ROOT / "项目数据" / "项目默认值库.json",
    ROOT / "项目数据" / "材料名库.json",
    ROOT / "项目数据" / "项目.json",
    ROOT / "标准知识库" / "已确认材料规则.json",
]

EMBEDDED_NAMES = [
    "PROCESS_KB",
    "MATERIAL_MAPPING_KB",
    "MATERIAL_CLASSIFICATION_KB",
    "LOSS_RATE_KB",
    "FORBIDDEN_OUTPUTS",
]


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def safe_rel(path: Path) -> Path:
    """Relative path within the project for manifest tracking."""
    try:
        return path.resolve().relative_to(ROOT)
    except ValueError:
        return Path(path.name)


def target_for_source(path: Path) -> Path:
    rel = safe_rel(path)
    if path.suffix.lower() == ".csv":
        rel = rel.with_suffix(".json")
    return OUT_ROOT / "sources" / rel


def read_csv_rows(path: Path) -> tuple[list[dict], list[str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
        fields = list(reader.fieldnames or [])
    return rows, fields


def read_json_payload(path: Path):
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def write_json(path: Path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))


def iter_source_files() -> list[Path]:
    files: list[Path] = []
    for source_dir in SOURCE_DIRS:
        if not source_dir.exists():
            continue
        for path in source_dir.rglob("*"):
            if path.is_file() and path.suffix.lower() in {".csv", ".json"}:
                files.append(path)
    for path in LOCAL_JSON_FILES:
        if path.exists():
            files.append(path)
    return sorted(set(files), key=lambda p: str(p))


def category_for(path: Path) -> str:
    text = str(path)
    if "开发者导出" in text:
        return "developer_export"
    if "03_国家规范库" in text:
        return "national_standards"
    if "01_定额库" in text:
        return "quota_library"
    if "房屋建筑与装饰工程" in text:
        return "building_decoration"
    if "通用安装工程" in text:
        return "installation_engineering"
    if "省份适配" in text:
        return "province_adapter"
    if "房建BOQ材料拆解规则库" in text:
        return "boq_decomposition_rules"
    if "品类树损耗率" in text:
        return "category_loss_rate"
    if "06_品类树" in text:
        return "category_tree"
    return "local_project"


def runtime_used(path: Path) -> bool:
    name = path.name
    return name in {
        "Q0_清单项目编码_房建工程.csv",
        "Q0_清单项目编码_安装工程.csv",
        "Q1_定额索引.csv",
        "Q2_定额材料消耗.csv",
        "Q3_定额材料映射.csv",
        "T3_房建_标准物料库.csv",
        "T3_安装_标准物料库.csv",
        "T3_品类映射.csv",
        "N1_国家规范索引.csv",
        "N3_材料技术参数定义.csv",
        "N5_材料规范映射.csv",
        "N6_规范校验规则.csv",
        "品类节点索引.csv",
        "项目默认值库.json",
        "材料名库.json",
        "已确认材料规则.json",
    }


def export_source(path: Path) -> dict:
    target = target_for_source(path)
    if path.suffix.lower() == ".csv":
        rows, fields = read_csv_rows(path)
        payload = {
            "meta": {
                "source_path": str(path),
                "source_format": "csv",
                "row_count": len(rows),
                "fields": fields,
                "sha256": sha256_file(path),
                "exported_at": datetime.now().isoformat(timespec="seconds"),
            },
            "rows": rows,
        }
        write_json(target, payload)
        row_count = len(rows)
        fields_count = len(fields)
    else:
        data = read_json_payload(path)
        payload = {
            "meta": {
                "source_path": str(path),
                "source_format": "json",
                "sha256": sha256_file(path),
                "exported_at": datetime.now().isoformat(timespec="seconds"),
            },
            "data": data,
        }
        write_json(target, payload)
        row_count = len(data) if isinstance(data, list) else (len(data) if isinstance(data, dict) else 1)
        fields_count = 0

    return {
        "source_abs_path": str(path.resolve()),
        "source_rel_path": str(safe_rel(path)),
        "source_format": path.suffix.lower().lstrip("."),
        "local_rel_path": str(target.relative_to(ROOT)),
        "category": category_for(path),
        "runtime_used": runtime_used(path),
        "size_bytes": path.stat().st_size,
        "sha256": sha256_file(path),
        "row_count": row_count,
        "fields_count": fields_count,
    }


def export_embedded_rules() -> list[dict]:
    script_path = ROOT / "流水线测试.py"
    tree = ast.parse(script_path.read_text(encoding="utf-8"))
    exported = []
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        targets = [t.id for t in node.targets if isinstance(t, ast.Name)]
        for name in targets:
            if name not in EMBEDDED_NAMES:
                continue
            value = ast.literal_eval(node.value)
            target = OUT_ROOT / "runtime_embedded" / f"{name.lower()}.json"
            payload = {
                "meta": {
                    "source_path": str(script_path),
                    "source_symbol": name,
                    "source_format": "python_literal",
                    "exported_at": datetime.now().isoformat(timespec="seconds"),
                },
                "data": value,
            }
            write_json(target, payload)
            exported.append(
                {
                    "source_abs_path": f"{script_path.resolve()}::{name}",
                    "source_rel_path": f"test_ai_pipeline.py::{name}",
                    "source_format": "python_literal",
                    "local_rel_path": str(target.relative_to(ROOT)),
                    "category": "runtime_embedded",
                    "runtime_used": True,
                    "size_bytes": target.stat().st_size,
                    "sha256": hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest(),
                    "row_count": len(value) if hasattr(value, "__len__") else 1,
                    "fields_count": 0,
                }
            )
    return exported


# ══════════════════════════════════════════════
# 索引构建 — O(1) 查找用
# ══════════════════════════════════════════════

索引目录 = OUT_ROOT / "索引"


def 构建索引() -> dict:
    """为高频查询表建立 ID→数据 的倒排索引，运行时 O(1) 查找。"""
    索引目录.mkdir(parents=True, exist_ok=True)
    started = time.time()
    stats = {}

    # ── Q1 定额索引: {quota_id: {行数据}} ──
    q1_csv = 源数据_DIR / "01_定额库" / "Q1_定额索引.csv"
    if q1_csv.exists():
        rows, _ = read_csv_rows(q1_csv)
        idx = {}
        for r in rows:
            qid = r.get("quota_id", "").strip()
            if qid:
                idx[qid] = r
        write_json(索引目录 / "q1_按ID.json", idx)
        stats["q1_按ID"] = len(idx)
        print(f"  索引 q1_按ID: {len(idx)} 条")

    # ── Q2 定额材料消耗: {quota_id: [{材料行}]} ──
    q2_csv = 源数据_DIR / "01_定额库" / "Q2_定额材料消耗.csv"
    if q2_csv.exists():
        rows, _ = read_csv_rows(q2_csv)
        idx: dict[str, list] = {}
        for r in rows:
            qid = r.get("quota_id", "").strip()
            if qid:
                idx.setdefault(qid, []).append(r)
        write_json(索引目录 / "q2_按定额ID.json", idx)
        stats["q2_按定额ID"] = len(idx)
        print(f"  索引 q2_按定额ID: {len(idx)} 个定额, {len(rows)} 条消耗")

    # ── T3 房建标准物料库: {物料ID: {行数据}} ──
    t3_fj_csv = 源数据_DIR / "01_房屋建筑与装饰工程" / "CSV导出" / "03_t3_标准物料库.csv"
    if t3_fj_csv.exists():
        rows, _ = read_csv_rows(t3_fj_csv)
        idx = {}
        for r in rows:
            mid = r.get("物料ID", "").strip()
            if mid:
                idx[mid] = r
        write_json(索引目录 / "t3_fj_按物料ID.json", idx)
        stats["t3_fj_按物料ID"] = len(idx)
        print(f"  索引 t3_fj_按物料ID: {len(idx)} 条")

    # ── T3 安装标准物料库 ──
    t3_az_csv = 源数据_DIR / "02_通用安装工程" / "CSV导出" / "02_t3_标准物料库.csv"
    if t3_az_csv.exists():
        rows, _ = read_csv_rows(t3_az_csv)
        idx = {}
        for r in rows:
            mid = r.get("物料ID", "").strip()
            if mid:
                idx[mid] = r
        write_json(索引目录 / "t3_az_按物料ID.json", idx)
        stats["t3_az_按物料ID"] = len(idx)
        print(f"  索引 t3_az_按物料ID: {len(idx)} 条")

    # ── 品类节点: {leaf_id: {行数据}} ──
    cat_csv = 源数据_DIR / "06_品类树" / "品类节点索引.csv"
    if cat_csv.exists():
        rows, _ = read_csv_rows(cat_csv)
        idx = {}
        for r in rows:
            lid = r.get("leaf_id", "").strip()
            if lid:
                idx[lid] = r
        write_json(索引目录 / "品类_按ID.json", idx)
        stats["品类_按ID"] = len(idx)
        print(f"  索引 品类_按ID: {len(idx)} 条")

    elapsed = time.time() - started
    print(f"索引构建完成: {len(stats)} 个索引, 耗时 {elapsed:.1f}s")
    return stats


def main() -> int:
    OUT_ROOT.mkdir(parents=True, exist_ok=True)
    entries = []
    for path in iter_source_files():
        entries.append(export_source(path))
    entries.extend(export_embedded_rules())

    manifest = {
        "meta": {
            "kb_package": "本地知识库包",
            "version": "2.0.0",
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "workspace": str(ROOT),
            "kb_root": str(源数据_DIR),
            "entry_count": len(entries),
            "runtime_used_count": sum(1 for e in entries if e.get("runtime_used")),
        },
        "entries": entries,
        "by_source_abs_path": {e["source_abs_path"]: e["local_rel_path"] for e in entries},
        "by_basename": {},
    }
    by_basename: dict[str, list[str]] = {}
    for e in entries:
        basename = Path(e["source_rel_path"].split("::")[0]).name
        by_basename.setdefault(basename, []).append(e["local_rel_path"])
    manifest["by_basename"] = by_basename
    write_json(OUT_ROOT / "manifest.json", manifest)

    total_rows = sum(e.get("row_count", 0) for e in entries if isinstance(e.get("row_count"), int))
    print(f"local kb json built: {len(entries)} files, {total_rows} rows/items")
    print(f"manifest: {OUT_ROOT / 'manifest.json'}")
    print(f"runtime used: {manifest['meta']['runtime_used_count']}")

    # 构建查询索引
    print()
    index_stats = 构建索引()
    manifest["meta"]["index_stats"] = index_stats
    write_json(OUT_ROOT / "manifest.json", manifest)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
