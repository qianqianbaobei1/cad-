# -*- coding: utf-8 -*-
"""一次系统表。整页缩略图会把柜号看串，这里先按表格线分列，再逐列抄。

单张系统图没有这么密的横竖线，检测不到表就不改原来的结果。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

import pymupdf


_MIN_RULES = 10
_GAP_BREAK = 0.04
_RUN_FRAC = 0.22
_DARK = 228
_COL_GAP = (0.018, 0.12)


@dataclass
class ScheduleTable:
    """一页里的一张表。坐标都是页面宽高的比例。"""

    rows: list[tuple[float, float]]
    columns: list[tuple[float, float]]
    page: int = 0
    # ruled[row][boundary] 为真，表示这根竖线穿过该行，格子在这里断开。
    ruled: list[list[bool]] = field(default_factory=list)

    def groups(self, row: int) -> list[list[int]]:
        groups: list[list[int]] = []
        for index in range(len(self.columns)):
            if index == 0 or self.ruled[row][index]:
                groups.append([index])
            else:
                groups[-1].append(index)
        return groups


def detect_schedules(pdf_path: str) -> list[ScheduleTable]:
    doc = pymupdf.open(pdf_path)
    try:
        tables: list[ScheduleTable] = []
        for page_index, page in enumerate(doc):
            for table in _page_tables(page):
                table.page = page_index
                tables.append(table)
        return tables
    finally:
        doc.close()


def _page_tables(page) -> list[ScheduleTable]:
    pix = page.get_pixmap(matrix=pymupdf.Matrix(3, 3), alpha=False)
    width, height, channels = pix.width, pix.height, pix.n
    samples = pix.samples
    rules = _horizontal_rules(samples, width, height, channels)
    bands: list[list[tuple[float, float, float]]] = []
    for rule in rules:
        if bands and rule[0] - bands[-1][-1][0] > _GAP_BREAK:
            bands.append([rule])
        elif bands:
            bands[-1].append(rule)
        else:
            bands.append([rule])
    tables = []
    for band in bands:
        if len(band) < _MIN_RULES:
            continue
        table = _table_from_band(samples, width, height, channels, band)
        if table is not None and len(table.columns) >= 4:
            tables.append(table)
    return tables


def _horizontal_rules(samples, width, height, channels) -> list[tuple[float, float, float]]:
    found = []
    row = None
    run = best = 0
    start = best_start = 0
    for y in range(height):
        run = best = 0
        for x in range(width):
            if samples[(y * width + x) * channels] < _DARK:
                if run == 0:
                    start = x
                run += 1
                if run > best:
                    best = run
                    best_start = start
            else:
                run = 0
        if best / width < _RUN_FRAC:
            if row:
                found.append(row)
                row = None
            continue
        yf = y / height
        x0, x1 = best_start / width, (best_start + best) / width
        if row and yf - row[0] <= 2 / height:
            if best > row[3]:
                row = (yf, x0, x1, best)
        else:
            if row:
                found.append(row)
            row = (yf, x0, x1, best)
    if row:
        found.append(row)
    return [(item[0], item[1], item[2]) for item in found]


def _table_from_band(samples, width, height, channels, band) -> ScheduleTable | None:
    y_lines = [item[0] for item in band]
    x_start = sorted(item[1] for item in band)[len(band) // 2]
    x_end = sorted(item[2] for item in band)[len(band) // 2]
    if x_end - x_start < 0.25:
        return None
    y0 = int(y_lines[0] * height)
    y1 = int(y_lines[-1] * height)
    verticals = _vertical_rules(samples, width, height, channels, y0, y1, x_start, x_end)
    chain = _column_chain(verticals)
    if len(chain) < 5:
        return None
    rows = list(zip(y_lines, y_lines[1:]))
    columns = list(zip(chain, chain[1:]))
    ruled = [
        _row_rules(samples, width, height, channels, top, bottom, chain)
        for top, bottom in rows
    ]
    return ScheduleTable(rows=rows, columns=columns, ruled=ruled)


def _vertical_rules(samples, width, height, channels, y0, y1, x_start, x_end) -> list[float]:
    span = max(1, y1 - y0)
    xs = []
    x_left = int(x_start * width)
    x_right = int(x_end * width) + 2
    for x in range(max(0, x_left), min(width, x_right)):
        run = best = 0
        for y in range(y0, y1):
            if samples[(y * width + x) * channels] < 232:
                run += 1
                best = max(best, run)
            else:
                run = 0
        if best / span >= 0.45:
            xs.append(x)
    groups: list[list[int]] = []
    for x in xs:
        if groups and x - groups[-1][-1] <= 3:
            groups[-1].append(x)
        else:
            groups.append([x])
    return [sum(group) / len(group) / width for group in groups]


def _column_chain(verticals: list[float]) -> list[float]:
    best: list[float] = []
    current: list[float] = []
    for x in verticals:
        if not current or _COL_GAP[0] <= x - current[-1] <= _COL_GAP[1]:
            current.append(x)
        else:
            if len(current) > len(best):
                best = current
            current = [x]
    if len(current) > len(best):
        best = current
    return best


def _row_rules(samples, width, height, channels, top, bottom, chain) -> list[bool]:
    y0 = int(top * height) + 2
    y1 = int(bottom * height) - 2
    if y1 <= y0:
        y0, y1 = int(top * height), int(bottom * height) + 1
    flags = []
    for xf in chain:
        base = int(round(xf * width))
        best_x = base
        best_score = 10 ** 9
        for x in range(max(0, base - 3), min(width, base + 4)):
            score = sum(samples[(y * width + x) * channels] for y in range(max(0, y0), min(height, y1)))
            if score < best_score:
                best_score = score
                best_x = x
        hits = total = 0
        for y in range(max(0, y0), min(height, y1)):
            total += 1
            value = samples[(y * width + best_x) * channels]
            left = min(
                samples[(y * width + max(0, best_x - 4)) * channels],
                samples[(y * width + max(0, best_x - 3)) * channels],
            )
            right = min(
                samples[(y * width + min(width - 1, best_x + 4)) * channels],
                samples[(y * width + min(width - 1, best_x + 3)) * channels],
            )
            if value < 235 and left > 240 and right > 240:
                hits += 1
        flags.append(bool(total) and hits / total > 0.6)
    return flags


def cell_role(text: str) -> str:
    compact = "".join(str(text or "").split())
    if "回路名称" in compact:
        return "load"
    if "回路编号" in compact:
        return "circuit_no"
    if "空气开关" in compact or "断路器" in compact:
        return "breaker"
    if "导线" in compact:
        return "cable"
    if "容量" in compact:
        return "power"
    if "编号" in compact and "回路" not in compact:
        return "code"
    return ""


def clean_cell(value) -> str:
    text = "" if value is None else str(value)
    text = text.replace("\\n", "\n")
    lines = []
    for line in text.splitlines():
        compact = "".join(line.split())
        if compact:
            lines.append(compact)
    return "\n".join(lines)


def _fill(values: list[str], groups: list[list[int]]) -> list[str]:
    filled = list(values)
    for group in groups:
        seen = []
        for index in group:
            if filled[index] and filled[index] not in seen:
                seen.append(filled[index])
        if len(seen) == 1:
            for index in group:
                filled[index] = seen[0]
    return filled


def label_roles(columns: list[list[str]], row_count: int) -> tuple[int | None, list[str]]:
    """找出行名那一列，并给出每一行该抄进哪个字段。"""
    roles = [""] * row_count
    label = None
    best_hits = 0
    for index, cells in enumerate(columns):
        found = [cell_role(cell) for cell in cells[:row_count]]
        hits = sum(1 for role in found if role)
        if hits > best_hits:
            best_hits = hits
            label = index
            roles = found + [""] * (row_count - len(found))
    if label is None or best_hits < 3:
        return None, [""] * row_count
    return label, roles[:row_count]


def realign_tall_row(table: ScheduleTable, columns: list[list[str]]) -> list[list[str]]:
    """电缆格往往高一倍。模型会把它拆成两格，后面的行名就整体下移一格。"""
    heights = [bottom - top for top, bottom in table.rows]
    if not heights:
        return columns
    median = sorted(heights)[len(heights) // 2]
    doubles = [index for index, height in enumerate(heights) if median and height >= median * 1.7]
    if len(doubles) != 1:
        return columns
    tall = doubles[0]
    _label, roles = label_roles(columns, len(table.rows))
    if "load" not in roles or roles.index("load") != tall + 3:
        return columns
    fixed = []
    for cells in columns:
        if len(cells) != len(table.rows):
            fixed.append(cells)
            continue
        cable = cells[tall]
        extra = str(cells[tall + 1] or "").strip()
        # 紧挨着的计算电流是数字，不要并进电缆。真正的第二行电缆带字母。
        if extra and re.search(r"[A-Za-z]", extra):
            cable = f"{cable}\n{extra}" if cable else extra
        fixed.append(cells[:tall] + [cable] + cells[tall + 2:] + [""])
    return fixed


def assign_columns(table: ScheduleTable, columns: list[list[str]]) -> tuple[list[dict], list[dict]]:
    """列文字已经按行对齐。返回箱体和回路，型号行不写入箱名。"""
    row_count = len(table.rows)
    aligned = []
    for cells in realign_tall_row(table, columns):
        cleaned = [clean_cell(item) for item in cells]
        if len(cleaned) != row_count:
            cleaned = [""] * row_count
        aligned.append(cleaned)
    while len(aligned) < len(table.columns):
        aligned.append([""] * row_count)

    label, roles = label_roles(aligned, row_count)
    if label is None or "code" not in roles:
        return [], []

    by_role: dict[str, list[str]] = {}
    for role in ("code", "breaker", "circuit_no", "cable", "load", "power"):
        row = roles.index(role) if role in roles else None
        values = [cells[row] if row is not None else "" for cells in aligned]
        if row is not None:
            values = _fill(values, table.groups(row))
        values[label] = ""
        by_role[role] = values

    boxes = []
    circuits = []
    seen = set()
    for index in range(len(table.columns)):
        if index == label:
            continue
        code = by_role["code"][index]
        if not code or cell_role(code):
            continue
        if code not in seen:
            seen.add(code)
            boxes.append({
                "code": code,
                "name": "",
                "quantity": 1,
                "ip_rating": "",
                "install": "",
                "location": "",
                "size": "",
                "quantity_confirmed": False,
            })
        circuit = {
            "box": code,
            "phase": "",
            "breaker": by_role["breaker"][index],
            "circuit_no": by_role["circuit_no"][index],
            "cable": by_role["cable"][index],
            "load_name": by_role["load"][index],
            "power_kw": by_role["power"][index],
        }
        if not any(circuit[key] for key in ("breaker", "circuit_no", "cable", "load_name", "power_kw")):
            continue
        circuits.append(circuit)
    return boxes, circuits
