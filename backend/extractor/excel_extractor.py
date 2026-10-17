# -*- coding: utf-8 -*-
"""配电箱设备清单 (Excel / CSV) 直接解析与组价引擎。

支持用户直接上传 Excel / CSV 格式的电气设备清单或 BOM 表，
跳过图纸图形识别环节，直接提取成套配电箱柜、回路与元器件规格，
并无缝接入后端的工业定额组价与品牌平替引擎。
"""

import os
import re
import csv
from typing import Any
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter

from .schema import RawExtraction, Box, Circuit, ExtraDevice, Uncertainty


def _clean_str(val: Any) -> str:
    if val is None:
        return ""
    return str(val).strip()


def _parse_num(val: Any, default: float = 1.0) -> float:
    if val is None:
        return default
    s = str(val).strip().replace("¥", "").replace("￥", "").replace(",", "")
    m = re.search(r"[-+]?\d*\.?\d+", s)
    if m:
        try:
            return float(m.group(0))
        except ValueError:
            pass
    return default


def _detect_encoding(file_path: str) -> str:
    """探测 CSV 文本编码 (UTF-8, GB18030 / GBK)"""
    for enc in ("utf-8-sig", "utf-8", "gb18030", "gbk"):
        try:
            with open(file_path, "r", encoding=enc) as f:
                f.read(4096)
            return enc
        except (UnicodeDecodeError, OSError):
            continue
    return "utf-8"


def _read_csv_rows(file_path: str) -> list[list[str]]:
    enc = _detect_encoding(file_path)
    rows = []
    with open(file_path, "r", encoding=enc, errors="replace") as f:
        reader = csv.reader(f)
        for r in reader:
            rows.append([_clean_str(c) for c in r])
    return rows


def _read_excel_sheets(file_path: str) -> list[tuple[str, list[list[str]]]]:
    wb = openpyxl.load_workbook(file_path, data_only=True)
    sheets = []
    for sname in wb.sheetnames:
        sheet_rows = []
        for r in wb[sname].iter_rows(values_only=True):
            sheet_rows.append([_clean_str(c) for c in r])
        if any(any(r) for r in sheet_rows):
            sheets.append((sname, sheet_rows))
    return sheets


def parse_equipment_bom_file(file_path: str) -> RawExtraction:
    """自动解析上传的设备清单 (Excel/CSV)，支持单 Sheet 或多 Sheet 混合格式。"""
    ext = os.path.splitext(file_path)[1].lower()
    if ext == ".csv":
        sheet_list = [("CSV", _read_csv_rows(file_path))]
    else:
        sheet_list = _read_excel_sheets(file_path)

    keys = {
        "box_code": ["箱号", "柜号", "配电箱编号", "设备编号", "箱体编号", "箱柜编号", "盘号", "所属箱柜"],
        "box_name": ["箱体名称", "箱名", "配电箱名称", "设备名称", "柜名", "箱柜名称"],
        "circuit_no": ["回路", "回路编号", "回路号", "支路编号", "支路", "回路名称"],
        "comp_name": ["元器件名称", "元件名称", "品名", "器件名称", "材料名称", "名称"],
        "spec": ["型号规格", "规格型号", "型号", "规格", "开关型号", "断路器型号", "参数"],
        "quantity": ["数量", "台数", "用量", "件数", "额定数量"],
        "unit": ["单位"],
        "load_name": ["负荷名称", "用途", "负载名称", "负载", "用电负荷"],
        "note": ["备注", "说明", "技术要求"]
    }

    boxes_dict: dict[str, Box] = {}
    circuits_list: list[Circuit] = []
    extra_devices_list: list[ExtraDevice] = []
    uncertainties_list: list[Uncertainty] = []

    current_box_code = "AL1"
    current_box_name = "配电箱"

    for sheet_name, all_rows in sheet_list:
        header_idx = -1
        col_map: dict[str, int] = {}

        for idx, r in enumerate(all_rows[:15]):
            hits = 0
            cur_map = {}
            for field, keywords in keys.items():
                for c_idx, cell in enumerate(r):
                    if any(kw == cell or kw in cell for kw in keywords):
                        if field not in cur_map:
                            cur_map[field] = c_idx
                            hits += 1
                            break
            if hits >= 2:
                header_idx = idx
                col_map = cur_map
                break

        if header_idx == -1 and all_rows:
            header_idx = 0
            r0 = all_rows[0]
            for c_idx, cell in enumerate(r0):
                for field, keywords in keys.items():
                    if any(kw in cell for kw in keywords) and field not in col_map:
                        col_map[field] = c_idx

        data_rows = all_rows[header_idx + 1:] if header_idx != -1 else all_rows

        for row_num, r in enumerate(data_rows, start=header_idx + 2):
            if not any(r):
                continue

            def get_val(f_key: str, r=r, col_map=col_map) -> str:
                if f_key in col_map and col_map[f_key] < len(r):
                    return r[col_map[f_key]]
                return ""

            row_box_code = get_val("box_code")
            row_box_name = get_val("box_name")
            row_circuit_no = get_val("circuit_no")
            row_comp_name = get_val("comp_name")
            row_spec = get_val("spec")
            row_qty = _parse_num(get_val("quantity"), default=1.0)
            row_unit = get_val("unit") or "只"
            row_load = get_val("load_name")
            row_note = get_val("note")

            if row_box_code:
                current_box_code = row_box_code
            if row_box_name:
                current_box_name = row_box_name

            if current_box_code not in boxes_dict:
                boxes_dict[current_box_code] = Box(
                    code=current_box_code,
                    name=current_box_name or f"{current_box_code} 配电箱",
                    quantity=int(row_qty) if row_qty >= 1 else 1
                )

            # 纯箱体行
            is_box_row = bool(
                (row_box_code or row_box_name)
                and not row_spec
                and not row_circuit_no
                and any(kw in (row_comp_name or row_box_name) for kw in ("箱", "柜", "配电箱", "控制箱"))
            )
            if is_box_row:
                boxes_dict[current_box_code].quantity = int(row_qty) if row_qty >= 1 else 1
                continue

            # 回路信息
            if row_circuit_no or row_spec:
                c = Circuit(
                    box=current_box_code,
                    circuit_no=row_circuit_no or f"W{len(circuits_list) + 1}",
                    load_name=row_load or row_comp_name or "配电分路",
                    breaker=row_spec,
                    note=row_note
                )
                circuits_list.append(c)

            # 元器件信息
            if row_spec or row_comp_name:
                c_name = row_comp_name or "微型断路器"
                if not row_comp_name and row_spec:
                    spec_up = row_spec.upper()
                    if any(k in spec_up for k in ("NM", "NSX", "CVS", "3VA", "CM1", "塑壳")):
                        c_name = "塑壳断路器"
                    elif any(k in spec_up for k in ("GL", "NH40", "OT", "OS", "隔离")):
                        c_name = "隔离开关"
                    elif any(k in spec_up for k in ("SPD", "电涌", "浪涌")):
                        c_name = "浪涌保护器"
                    elif any(k in spec_up for k in ("KM", "CJX2", "LC1", "接触器")):
                        c_name = "交流接触器"

                used_ref = f"{current_box_code} {row_circuit_no}".strip() if row_circuit_no else current_box_code
                dev = ExtraDevice(
                    name=c_name,
                    spec=row_spec or c_name,
                    unit=row_unit or "只",
                    quantity=max(1.0, row_qty),
                    used_in=used_ref,
                    note=row_note
                )
                extra_devices_list.append(dev)

    if not boxes_dict:
        boxes_dict["AL1"] = Box(code="AL1", name="标准照明配电箱", quantity=1)
        uncertainties_list.append(Uncertainty(
            location="清单解析",
            detail="上传的清单中未检测到明确箱号与器件规格，已创建默认 AL1 待核对",
            source="program"
        ))

    return RawExtraction(
        boxes=list(boxes_dict.values()),
        circuits=circuits_list,
        extra_devices=extra_devices_list,
        requirements=[],
        uncertainties=uncertainties_list
    )


def create_standard_bom_template(output_path: str) -> str:
    """创建专业、符合成套厂标准的《标准配电箱设备清单模板.xlsx》"""
    wb = openpyxl.Workbook()
    # 移除默认 sheet
    ws_boxes = wb.active
    ws_boxes.title = "配电箱清单"
    ws_parts = wb.create_sheet(title="元器件及回路清单")

    # 样式配置：咨询级 Navy / Ice Blue 风格
    navy_fill = PatternFill(start_color="1E40AF", end_color="1E40AF", fill_type="solid")
    teal_fill = PatternFill(start_color="00554E", end_color="00554E", fill_type="solid")
    header_font = Font(name="PingFang SC", size=10, bold=True, color="FFFFFF")
    title_font = Font(name="PingFang SC", size=13, bold=True, color="0F172A")
    data_font = Font(name="PingFang SC", size=9.5)
    code_font = Font(name="Consolas", size=9.5)
    border_thin = Side(style="thin", color="CBD5E1")
    box_border = Border(left=border_thin, right=border_thin, top=border_thin, bottom=border_thin)

    # 1. Sheet 1: 配电箱清单
    ws_boxes.merge_cells("A1:G1")
    ws_boxes["A1"] = "工程成套配电箱柜一览表（模板范例，可直接修改或增加行）"
    ws_boxes["A1"].font = title_font
    ws_boxes["A1"].alignment = Alignment(vertical="center", indent=1)
    ws_boxes.row_dimensions[1].height = 32

    box_headers = ["箱柜编号*", "箱体名称*", "安装方式", "防护等级", "外形尺寸 (宽×高×深 mm)", "台数*", "备注说明"]
    for col_idx, h in enumerate(box_headers, start=1):
        cell = ws_boxes.cell(row=2, column=col_idx, value=h)
        cell.fill = navy_fill
        cell.font = header_font
        cell.alignment = Alignment(horizontal="center", vertical="center")
        cell.border = box_border
    ws_boxes.row_dimensions[2].height = 24

    sample_boxes = [
        ["1AL1", "一层照明配电箱", "嵌墙暗装", "IP4X", "500×600×160", 1, "地下室照明电源"],
        ["1AP1", "地下水泵动力配电柜", "落地安装", "IP54", "800×1800×600", 2, "一用一备排污泵"],
        ["2AL2", "二层应急照明双电源箱", "明装挂墙", "IP44", "600×800×180", 1, "双电源自动切换"],
    ]
    for r_idx, row_data in enumerate(sample_boxes, start=3):
        ws_boxes.row_dimensions[r_idx].height = 20
        for c_idx, val in enumerate(row_data, start=1):
            cell = ws_boxes.cell(row=r_idx, column=c_idx, value=val)
            cell.font = code_font if c_idx in (1, 5, 6) else data_font
            cell.alignment = Alignment(horizontal="center" if c_idx in (1, 3, 4, 6) else "left", vertical="center")
            cell.border = box_border

    # 2. Sheet 2: 元器件及回路清单
    ws_parts.merge_cells("A1:H1")
    ws_parts["A1"] = "配电箱内元器件与回路配置明细（支持直接粘贴成套厂现有报价清单）"
    ws_parts["A1"].font = title_font
    ws_parts["A1"].alignment = Alignment(vertical="center", indent=1)
    ws_parts.row_dimensions[1].height = 32

    part_headers = ["所属箱柜*", "回路编号", "负荷/用途名称", "元器件名称*", "型号规格*", "数量*", "单位", "备注说明"]
    for col_idx, h in enumerate(part_headers, start=1):
        cell = ws_parts.cell(row=2, column=col_idx, value=h)
        cell.fill = teal_fill
        cell.font = header_font
        cell.alignment = Alignment(horizontal="center", vertical="center")
        cell.border = box_border
    ws_parts.row_dimensions[2].height = 24

    sample_parts = [
        ["1AL1", "进线", "电源进线", "隔离开关", "GL-63A/3P", 1, "只", "进线总隔离"],
        ["1AL1", "进线侧", "电源防雷", "电涌保护器", "SPD 4P 40kA", 1, "套", "厂家配套"],
        ["1AL1", "WL1", "办公区照明", "微型断路器", "C65N-C16A/1P", 1, "只", ""],
        ["1AL1", "WL2", "走道公共照明", "微型断路器", "C65N-C16A/1P", 1, "只", ""],
        ["1AL1", "WN1", "插座回路", "漏电断路器", "C65N-C20A/2P+Vigi", 2, "只", "30mA瞬动"],
        ["1AL1", "备用", "备用回路", "微型断路器", "C65N-C16A/1P", 1, "只", "已安装备用"],
        ["1AP1", "进线", "动力电源进线", "塑壳断路器", "NM1-125S/3300 100A", 1, "只", "主开关"],
        ["1AP1", "WP1", "1#排污泵主回路", "交流接触器", "CJX2-1810 220V", 1, "只", "配热过载继电器"],
        ["1AP1", "WP2", "2#排污泵主回路", "交流接触器", "CJX2-1810 220V", 1, "只", "配热过载继电器"],
        ["2AL2", "进线", "双电源供电", "双电源自动转换开关", "ATSE 63A/4P PC级", 1, "台", "自动投切"],
    ]
    for r_idx, row_data in enumerate(sample_parts, start=3):
        ws_parts.row_dimensions[r_idx].height = 20
        for c_idx, val in enumerate(row_data, start=1):
            cell = ws_parts.cell(row=r_idx, column=c_idx, value=val)
            cell.font = code_font if c_idx in (1, 2, 5, 6) else data_font
            cell.alignment = Alignment(horizontal="center" if c_idx in (1, 2, 6, 7) else "left", vertical="center")
            cell.border = box_border

    # 列宽自适应
    for ws in (ws_boxes, ws_parts):
        for col in ws.columns:
            max_len = max(len(str(cell.value or '')) for cell in col)
            col_letter = get_column_letter(col[0].column)
            ws.column_dimensions[col_letter].width = max(max_len * 1.8, 14)

    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    wb.save(output_path)
    return output_path
