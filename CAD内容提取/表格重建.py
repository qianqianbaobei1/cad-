#!/usr/bin/env python3
"""
从 dwgread JSON 中提取「室内外装修做法表」并按坐标重建表格。
提取地面/楼面相关做法 —— MVP 的核心材料数据。

用法：
    python3 reconstruct_table.py <dump.json> [-o output_dir]
    python3 reconstruct_table.py output/建筑/xxx_dump.json
    python3 reconstruct_table.py output/建筑/xxx_dump.json -o output/建筑
"""
import json, sys, os, argparse
from pathlib import Path

# ── 配置 ──────────────────────────────
ROW_Y_TOLERANCE = 600          # y 坐标容差（CAD单位）
CELL_X_TOLERANCE = 300         # x 坐标容差
COLUMN_MIN_GAP = 800           # 列间最小 x 间隙

SECTION_KEYWORDS = [
    "室内外装修做法表", "材料做法表",
    "地面做法", "楼面做法", "屋面做法",
    "内墙做法", "外墙做法", "顶棚做法", "踢脚做法",
]


def load_json(json_path: str) -> dict:
    print(f"加载 JSON: {json_path}")
    with open(json_path, encoding="utf-8", errors="replace") as f:
        return json.load(f)


def build_layer_map(data: dict) -> dict:
    """构建图层句柄 → 图层名映射"""
    print("构建图层映射...")
    layer_map = {}
    for obj in data.get("OBJECTS", []):
        if not isinstance(obj, dict):
            continue
        if obj.get("entity") == "LAYER" or obj.get("object") == "LAYER":
            handle = tuple(obj.get("handle", []))
            name = obj.get("name", obj.get("layer_name", ""))
            if handle and name:
                layer_map[handle] = name
        if "name" in obj and "handle" in obj:
            name = obj["name"]
            if isinstance(name, str) and name.strip():
                handle = tuple(obj["handle"])
                if "(" not in name and "=" not in name and len(name) < 50:
                    layer_map[handle] = name
    print(f"  图层映射: {len(layer_map)} 条")
    return layer_map


def resolve_layer(layer_ref, layer_map: dict, data: dict) -> str:
    if isinstance(layer_ref, dict):
        layer_ref = list(layer_ref.values()) if layer_ref else []
    if isinstance(layer_ref, list):
        key = tuple(layer_ref)
        if key in layer_map:
            return layer_map[key]
        if len(layer_ref) >= 3 and layer_ref[0] in (5, 4):
            obj_idx = layer_ref[-1] if len(layer_ref) > 2 else layer_ref[1]
            objects = data.get("OBJECTS", [])
            if 0 <= obj_idx < len(objects):
                obj = objects[obj_idx]
                if isinstance(obj, dict) and "name" in obj:
                    return str(obj["name"])
    return str(layer_ref)


def extract_text_entities(data: dict, layer_map: dict) -> list:
    """从 OBJECTS 提取所有带坐标的 TEXT 实体"""
    print("提取 TEXT 实体...")
    text_entities = []
    for obj in data.get("OBJECTS", []):
        if not isinstance(obj, dict):
            continue
        if obj.get("entity") == "TEXT" and "text_value" in obj:
            ins = obj.get("ins_pt")
            if ins and len(ins) >= 2:
                text_entities.append({
                    "text": obj["text_value"],
                    "x": ins[0],
                    "y": ins[1],
                    "layer": resolve_layer(obj.get("layer"), layer_map, data),
                    "handle": tuple(obj.get("handle", [])),
                    "height": obj.get("height", 0),
                })
    print(f"  TEXT 实体: {len(text_entities)} 条（含坐标）")
    return text_entities


def find_table_anchors(text_entities: list) -> list:
    """找到所有做法表标题位置"""
    text_entities.sort(key=lambda t: (-t["y"], t["x"]))
    anchors = []
    for i, t in enumerate(text_entities):
        if "室内外装修做法表" in t["text"]:
            anchors.append(t)
        # 也收集"材料做法表"作为独立锚点
        if "材料做法表" in t["text"] and "室内外" not in t["text"]:
            anchors.append(t)
    return anchors


def extract_region(texts, anchor_y, anchor_x, y_range=(-50000, 10000), x_margin=150000):
    y_min = anchor_y + y_range[0]
    y_max = anchor_y + y_range[1]
    return [t for t in texts if y_min <= t["y"] <= y_max and abs(t["x"] - anchor_x) < x_margin]


def cluster_by_rows(texts, tolerance=ROW_Y_TOLERANCE):
    if not texts:
        return []
    sorted_texts = sorted(texts, key=lambda t: -t["y"])
    rows = []
    current_row = [sorted_texts[0]]
    current_y = sorted_texts[0]["y"]
    for t in sorted_texts[1:]:
        if abs(t["y"] - current_y) <= tolerance:
            current_row.append(t)
        else:
            rows.append(sorted(current_row, key=lambda x: x["x"]))
            current_row = [t]
            current_y = t["y"]
    rows.append(sorted(current_row, key=lambda x: x["x"]))
    return rows


def merge_row_cells(row, tolerance=CELL_X_TOLERANCE):
    if not row:
        return []
    cells = []
    current_cell = [row[0]]
    for t in row[1:]:
        if abs(t["x"] - current_cell[-1]["x"]) <= tolerance:
            current_cell.append(t)
        else:
            cells.append(current_cell)
            current_cell = [t]
    cells.append(current_cell)
    return ["".join(c["text"] for c in cell) for cell in cells]


def find_sections_in_region(region_texts):
    """找到各章节标题及其坐标"""
    sections = {}
    for kw in [
        "屋面做法", "地面做法", "楼面做法", "内墙做法",
        "外墙做法", "顶棚做法", "踢脚做法",
        "材料做法表（楼地面）", "材料做法表（内墙面）",
        "材料做法表（外墙面）", "材料做法表（屋面）", "材料做法表（顶棚）",
    ]:
        candidates = [t for t in region_texts if kw in t["text"]]
        if candidates:
            avg_x = sum(t["x"] for t in candidates) / len(candidates)
            avg_y = max(t["y"] for t in candidates)
            sections[kw] = (avg_x, avg_y)
    return sections


def extract_section_table(region_texts, section_x, section_y, section_height=65000, x_width=40000):
    y_min = section_y - section_height
    y_max = section_y + 2000
    x_min = section_x - x_width
    x_max = section_x + x_width
    col_texts = [t for t in region_texts
                 if y_min <= t["y"] <= y_max and x_min <= t["x"] <= x_max]
    rows = cluster_by_rows(col_texts)
    return [merge_row_cells(row) for row in rows]


def extract_ground_floor_sections(region_texts, anchor_x, anchor_y):
    """提取地面/楼面做法章节"""
    sections = find_sections_in_region(region_texts)
    results = {}

    for section_name in ["地面做法", "楼面做法"]:
        if section_name not in sections:
            continue

        sx, sy = sections[section_name]
        table_rows = extract_section_table(region_texts, sx + 20000, sy,
                                           section_height=65000, x_width=80000)

        # 找表头行
        header_idx = None
        data_start_idx = None
        for i, row in enumerate(table_rows):
            row_text = "".join(row)
            if "编号" in row_text and "分层做法" in row_text:
                header_idx = i
            if header_idx is not None and i > header_idx:
                if not any("编号" in cell for cell in row):
                    data_start_idx = i
                    break

        if data_start_idx is None:
            continue

        data_rows = table_rows[data_start_idx:]
        clean_rows = []
        for row in data_rows:
            row_text = "".join(row)
            if any(f"{c}、" in row_text for c in "一二三四五六七八九十" if f"{c}、" not in section_name):
                break
            if any(w in row_text for w in ["1:100", "建筑设计总说明"]):
                continue
            clean_rows.append(row)

        results[section_name] = {
            "header_x": sx,
            "header_y": sy,
            "rows": clean_rows,
        }

    # 也处理「材料做法表（楼地面）」——住宅楼专用格式
    if "材料做法表（楼地面）" in sections:
        sx, sy = sections["材料做法表（楼地面）"]
        table_rows = extract_section_table(region_texts, sx + 20000, sy,
                                           section_height=65000, x_width=80000)
        # 材料做法表通常有不同的表头：编号 | 名称 | 做法 | 备注
        # 取表头之后的所有行
        data_start_idx = 0
        for i, row in enumerate(table_rows):
            row_text = "".join(row)
            if any(h in row_text for h in ["编号", "名称", "做法"]):
                data_start_idx = i + 1
                break

        data_rows = table_rows[data_start_idx:]
        clean_rows = []
        for row in data_rows:
            row_text = "".join(row)
            if any(f"{c}、" in row_text for c in "一二三四五六七八九十"):
                break
            if any(w in row_text for w in ["1:100", "建筑设计总说明", "室内外装修做法表"]):
                continue
            clean_rows.append(row)

        # 用 "材料做法表（楼地面）" 作为 key；在后处理中会映射到 "地面做法" 或新增分类
        # 实际内容通常是楼地面做法
        results["材料做法表（楼地面）"] = {
            "header_x": sx,
            "header_y": sy,
            "rows": clean_rows,
        }

    return results


def extract_residential_format(text_entities, anchor, x_width=20000, y_range=(-50000, 5000)):
    """
    住宅楼格式：多个「材料做法表」横向排列。
    用窄 X 范围精准切出「材料做法表（楼地面）」列。
    """
    ax, ay = anchor["x"], anchor["y"]
    region = [t for t in text_entities
              if ay + y_range[0] <= t["y"] <= ay + y_range[1]
              and ax - x_width <= t["x"] <= ax + x_width]

    rows = cluster_by_rows(region)
    # 过滤掉标题行和太短的行
    data_rows = []
    for row in rows:
        cells = merge_row_cells(row)
        combined = "".join(cells)
        # 跳过仅有标题的行
        if combined.strip() in ("材料做法表（楼地面）", "《室内外装修做法表》", "室内外装修做法表"):
            continue
        if len(combined) < 3:
            continue
        data_rows.append(cells)

    return data_rows


def main():
    parser = argparse.ArgumentParser(description="从 DWG dump JSON 重建做法表")
    parser.add_argument("json_path", help="dwgread 生成的 dump JSON 路径")
    parser.add_argument("-o", "--output-dir", default=None, help="输出目录（默认与 JSON 同目录）")
    parser.add_argument("--building-name", default=None, help="楼栋名称（用于输出文件命名）")
    args = parser.parse_args()

    json_path = args.json_path
    if not os.path.exists(json_path):
        print(f"文件不存在: {json_path}")
        sys.exit(1)

    output_dir = args.output_dir or os.path.dirname(json_path)
    os.makedirs(output_dir, exist_ok=True)
    building_name = args.building_name or Path(json_path).stem.replace("_dump", "")

    data = load_json(json_path)
    layer_map = build_layer_map(data)
    text_entities = extract_text_entities(data, layer_map)

    anchors = find_table_anchors(text_entities)
    print(f"\n找到 {len(anchors)} 个做法表锚点:")
    for i, a in enumerate(anchors):
        print(f"  [{i}] x={a['x']:.0f} y={a['y']:.0f} → {a['text'][:60]}")

    all_sections = {}
    section_counter = 0

    # ── 策略 A：商业楼格式（室内外装修做法表 → 地面做法/楼面做法 章节）──
    for anchor_idx, anchor in enumerate(anchors):
        if "材料做法表" in anchor["text"] and "室内外" not in anchor["text"]:
            continue  # 留给策略 B 处理

        region = extract_region(text_entities, anchor["y"], anchor["x"])
        gf_sections = extract_ground_floor_sections(region, anchor["x"], anchor["y"])

        for section_name, section_data in gf_sections.items():
            print(f"\n  [{section_name}] {len(section_data['rows'])} 行 (商业楼格式):")
            for i, row in enumerate(section_data['rows']):
                combined = " | ".join(row)
                print(f"    [{i}] {combined[:200]}")

            out_path = Path(output_dir) / f"地面楼面做法_{section_counter}.json"
            with open(out_path, "w", encoding="utf-8") as f:
                json.dump({section_name: section_data}, f, ensure_ascii=False, indent=2)
            print(f"  已保存: {out_path}")

            all_sections[f"section_{section_counter}"] = {
                "anchor_idx": anchor_idx,
                "section_name": section_name,
                "file": str(out_path),
                "row_count": len(section_data['rows']),
                "format": "commercial",
            }
            section_counter += 1

    # ── 策略 B：住宅楼格式（材料做法表（楼地面）→ 窄 X 范围提取）──
    residential_anchors = [a for a in anchors if "材料做法表" in a["text"] and "楼地面" in a["text"]]
    for anchor in residential_anchors:
        print(f"\n{'='*60}")
        print(f"住宅楼格式: 材料做法表（楼地面） (x={anchor['x']:.0f} y={anchor['y']:.0f})")

        rows = extract_residential_format(text_entities, anchor)
        print(f"  提取 {len(rows)} 行")

        section_data = {
            "header_x": anchor["x"],
            "header_y": anchor["y"],
            "format": "residential",
            "rows": rows,
        }

        for i, row in enumerate(rows):
            combined = " | ".join(row)
            print(f"    [{i}] {combined[:200]}")

        out_path = Path(output_dir) / f"地面楼面做法_{section_counter}.json"
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump({"材料做法表（楼地面）": section_data}, f, ensure_ascii=False, indent=2)
        print(f"  已保存: {out_path}")

        all_sections[f"section_{section_counter}"] = {
            "section_name": "材料做法表（楼地面）",
            "file": str(out_path),
            "row_count": len(rows),
            "format": "residential",
        }
        section_counter += 1

    # 汇总
    summary_path = Path(output_dir) / f"{building_name}_做法表索引.json"
    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump({
            "building": building_name,
            "json_source": json_path,
            "total_sections": section_counter,
            "sections": all_sections,
        }, f, ensure_ascii=False, indent=2)
    print(f"\n索引已保存: {summary_path}")
    print(f"完成：共提取 {section_counter} 个地面/楼面做法章节")


if __name__ == "__main__":
    main()
