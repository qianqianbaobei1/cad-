#!/usr/bin/env python3
"""构建 O(1) 查询索引 — 从 标准知识库/源数据/ CSV 直接建索引，无 JSON 镜像。"""
from __future__ import annotations

import csv
import json
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
源数据_DIR = ROOT / "标准知识库" / "源数据"
OUT_ROOT = ROOT / "项目数据" / "本地知识库包"
索引目录 = OUT_ROOT / "索引"


def read_csv_rows(path: Path) -> tuple[list[dict], list[str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
        fields = list(reader.fieldnames or [])
    return rows, fields


def write_json(path: Path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))


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
    index_stats = 构建索引()
    print(f"\n索引目录: {索引目录}")
    print(f"索引总大小: {sum(f.stat().st_size for f in 索引目录.rglob('*') if f.is_file()):,} bytes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
