# -*- coding: utf-8 -*-
"""Build the quotation workbook from an ExtractionResult."""
import openpyxl
import os
import re
from typing import Any
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter

from .schema import ExtractionResult
from .config import delivery as _delivery
from .quote_calc import quote_box, quote_project

# 交付样式全部来自 config/delivery.json：换配色、换表名、换默认平替品牌都只改配置。
_DELIVERY = _delivery()
_FONT_FAMILY = _DELIVERY["fonts"]["family"]
_FONT_LATIN = _DELIVERY["fonts"]["latin"]
_PALETTE = _PALETTE = _DELIVERY["palette"]
_SHEETS = _DELIVERY["sheets"]
_COLUMNS = _DELIVERY["columns"]
_LABELS = _DELIVERY["labels"]
BASE_TITLE = _LABELS["base_title"]
DEFAULT_TARGET_BRAND = _DELIVERY["brand"]["default_target"]
GENERIC_BRAND = _DELIVERY["brand"]["unknown"]
_EXPORT_CFG = _DELIVERY["export_gate"]
_ESTIMATION = _DELIVERY["estimation"]


def uncertainty_texts(result: ExtractionResult, source: str | None = None) -> list[str]:
    """导出与旧接口沿用的单行写法，已确认项加前缀。source 过滤模型事实或程序告警。"""
    return [f"（已确认）{item.text}" if item.resolved else item.text
            for item in result.uncertainties
            if item.text and (source is None or item.source == source)]

HDR_FONT = Font(name=_FONT_FAMILY, size=11, bold=True, color=_PALETTE["header_text"])
HDR_FILL = PatternFill("solid", fgColor=_PALETTE["header_fill"])
TITLE_FONT = Font(name=_FONT_FAMILY, size=14, bold=True)
SUB_FONT = Font(name=_FONT_FAMILY, size=10, color=_PALETTE["sub_text"])
META_FONT = Font(name=_FONT_FAMILY, size=10, color=_PALETTE["meta_text"])
CELL_FONT = Font(name=_FONT_FAMILY, size=10)
thin = Side(style="thin", color=_PALETTE["border_light"])
dark_thin = Side(style="thin", color=_PALETTE["border_dark"])
BORDER = Border(left=thin, right=thin, top=thin, bottom=thin)
DARK_BORDER = Border(left=dark_thin, right=dark_thin, top=dark_thin, bottom=dark_thin)
DOUBLE_BOTTOM_BORDER = Border(left=dark_thin, right=dark_thin, top=dark_thin, bottom=Side(style="double", color=_PALETTE["meta_text"]))
CENTER = Alignment(horizontal="center", vertical="center", wrap_text=True)
LEFT = Alignment(horizontal="left", vertical="center", wrap_text=True)
RIGHT = Alignment(horizontal="right", vertical="center", wrap_text=True)

# 行业级成套设备报价规范配色（对标行业实际成套厂出图标准）
SUMMARY_TITLE_FONT = Font(name=_FONT_FAMILY, size=16, bold=True)
SUMMARY_HDR_FILL = PatternFill("solid", fgColor=_PALETTE["summary_header_fill"])  # 截图同款中浅灰表头
SUMMARY_HDR_FONT = Font(name=_FONT_FAMILY, size=10, bold=True, color=_PALETTE["meta_text"])
CATEGORY_FILL = PatternFill("solid", fgColor=_PALETTE["category_fill"])  # 截图同款配电箱绿色条目
CATEGORY_FONT = Font(name=_FONT_FAMILY, size=11, bold=True, color=_PALETTE["category_text"])
CARD_HDR_FILL = PatternFill("solid", fgColor=_PALETTE["card_header_fill"])  # 垂直流水卡片深蓝标题栏
CARD_SUB_FILL = PatternFill("solid", fgColor=_PALETTE["card_sub_fill"])  # 成本小计淡浅蓝条
TOTAL_FILL = PatternFill("solid", fgColor=_PALETTE["total_fill"])
TOTAL_FONT = Font(name=_FONT_FAMILY, size=10, bold=True)
LINK_FONT = Font(name=_FONT_FAMILY, size=10, color=_PALETTE["link_text"], underline="single", bold=True)
WHITE_BOLD_FONT = Font(name=_FONT_FAMILY, size=10, bold=True, color=_PALETTE["header_text"])
DARK_BOLD_FONT = Font(name=_FONT_FAMILY, size=10, bold=True, color=_PALETTE["dark_bold_text"])
BOX_HEADER_FILL = PatternFill("solid", fgColor=_PALETTE["box_header_fill"])  # 截图同款水蓝色箱头横幅（淡青水蓝）
BOX_HEADER_FONT = Font(name=_FONT_FAMILY, size=10, bold=True, color=_PALETTE["meta_text"])
ORANGE_FILL = PatternFill("solid", fgColor=_PALETTE["orange_fill"])  # 截图同款单台合计与总计浅橙底纹
ORANGE_FONT = Font(name=_FONT_FAMILY, size=10, bold=True, color=_PALETTE["meta_text"])


def _val(obj: Any, key: str, default: Any = "") -> Any:
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def sanitize_excel_value(val: Any) -> Any:
    """防范 Excel / CSV 公式注入与 DDE 命令执行，并清洗 openpyxl 不支持的非法控制字符。"""
    if isinstance(val, str) and val:
        from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE
        val = ILLEGAL_CHARACTERS_RE.sub("", val)
        if not val:
            return ""
        if val[0] in ('=', '+', '-', '@', '\t', '\r'):
            # 允许受信任的安全内部统计公式（如 SUM, ROUND 等受控格式），阻断命令或外部 DDE 执行
            is_internal_formula = (
                val.startswith("=SUM(") or 
                val.startswith("=ROUND(") or 
                val.startswith("='") or 
                (val.startswith("=") and any(ch.isalpha() for ch in val[:4]) and not any(k in val.lower() for k in ("cmd", "powershell", "http", "dde", "exec", "mshta", "regsvr32", "|")))
            )
            if not is_internal_formula:
                return "'" + val
    return val


def _build_comps_by_box(components: list[Any]) -> dict[str, list[Any]]:
    """将元器件按所属箱体分组，支持 '1AP1、1AP2' 跨箱多箱映射。"""
    comps_by_box: dict[str, list[Any]] = {}
    for comp in components:
        used = getattr(comp, "used_in", "") if hasattr(comp, "used_in") else str(comp.get("used_in", ""))
        parts = [p.strip() for p in re.split(r"[、,，\s]+", used) if p.strip()]
        if not parts and used:
            parts = [used.strip()]
        for p in parts:
            comps_by_box.setdefault(p, []).append(comp)
        if used and used not in comps_by_box:
            comps_by_box.setdefault(used, []).append(comp)
    return comps_by_box


def _estimate_box_costs(box: Any, box_circuits: list[Any], box_components: list[Any], brand: str = "") -> dict[str, float]:
    """成套配电箱成本构成测算模型：
    1. 箱体外壳费 (box_shell)：依据安装方式、回路数及落地/明暗装尺寸估算钣金喷塑外壳；
    2. 元器件总额 (comp_total)：基于规范化参数与品牌集采折率精准计算；
    3. 辅材母排费 (bus_total)：依据进线电流匹配 TM 铜排截面与重量动态联动铜价；
    4. 安装试验工时 (labor_total)：一二次线缆组装、辅材与耐压打压测试；
    5. 单台成套单价 (unit_price) = 全项含税工业造价。
    """
    from .pricing import calculate_box_quotation

    b_dict = {
        "box_code": str(_val(box, "code", "")),
        "box_name": str(_val(box, "name", "")),
        "install_type": str(_val(box, "install", "")),
        "box_type": str(_val(box, "size", "")),
        "ip_rating": str(_val(box, "ip_rating", "")),
    }
    circ_dicts = []
    for c in box_circuits:
        circ_dicts.append({
            "circuit_type": str(_val(c, "circuit_type", "")),
            "breaker_spec": str(_val(c, "breaker", "")),
            "load_name": str(_val(c, "load_name", "")),
            "circuit_no": str(_val(c, "circuit_no", "")),
        })
    comp_dicts = []
    # 1. 显式提取回路中的开关断路器（进线及各分支回路出线）并计入元件清单
    for c in box_circuits:
        brk_spec = str(_val(c, "breaker", "")).strip()
        if not brk_spec or brk_spec in ("-", "待确认"):
            continue
        c_type = str(_val(c, "circuit_type", "")).strip()
        load_name = str(_val(c, "load_name", "")).strip()
        c_no = str(_val(c, "circuit_no", "")).strip()
        is_inc = c_type == "incoming" or "进线" in load_name or "进线" in c_no
        dev_name = "进线断路器" if is_inc else "分支断路器"
        comp_dicts.append({
            "name": dev_name,
            "spec": brk_spec,
            "quantity": 1,
        })

    # 2. 计入箱体关联的非回路或独立元器件 (如电涌保护器 SPD、电表、信号灯等)
    for comp in box_components:
        c_spec = str(_val(comp, "spec", "")).strip()
        if not c_spec or c_spec == "-":
            continue
        comp_dicts.append({
            "name": str(_val(comp, "name", "")),
            "spec": c_spec,
            "quantity": _val(comp, "quantity", 1),
        })

    # 宁可标疑、不许编造：不再自动补入虚构的双电源切换开关。
    # （原"领域智能推断"按柜号/回路数猜 ATS 规格并计入报价，属编造，已删除）

    q = calculate_box_quotation(b_dict, circ_dicts, comp_dicts, brand=brand or DEFAULT_TARGET_BRAND)
    cb = q["cost_breakdown"]
    return {
        "box_shell": cb["enclosure_cost"],
        "comp_total": cb["component_cost"],
        "bus_total": cb["copper_busbar_cost"],
        "labor_total": round(cb["labor_cost"] + cb["auxiliary_cost"] + cb["test_cert_cost"], 2),
        "unit_price": q["final_tax_included"],
    }


def _setup(ws, title, subtitle, headers, widths):
    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=len(headers))
    c = ws.cell(row=1, column=1, value=title)
    c.font = TITLE_FONT
    c.alignment = Alignment(horizontal="center", vertical="center")
    ws.row_dimensions[1].height = 30
    ws.merge_cells(start_row=2, start_column=1, end_row=2, end_column=len(headers))
    c = ws.cell(row=2, column=1, value=subtitle)
    c.font = SUB_FONT
    c.alignment = Alignment(horizontal="center", vertical="center")
    ws.row_dimensions[2].height = 20
    for j, h in enumerate(headers, start=1):
        c = ws.cell(row=3, column=j, value=h)
        c.font = HDR_FONT
        c.fill = HDR_FILL
        c.alignment = CENTER
        c.border = BORDER
    ws.row_dimensions[3].height = 28
    for j, w in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(j)].width = w
    ws.sheet_properties.pageSetUpPr = openpyxl.worksheet.properties.PageSetupProperties(fitToPage=True)
    ws.page_setup.orientation = "landscape"
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 0
    ws.print_title_rows = "1:3"
    return 4


def _row(ws, r, values, height=36, center_cols=(1, 2), template=False):
    for j, v in enumerate(values, start=1):
        c = ws.cell(row=r, column=j, value=sanitize_excel_value(v))
        if not template:  # 自定义模板自带样式，只填值
            c.font = CELL_FONT
            c.alignment = CENTER if j in center_cols else LEFT
            c.border = BORDER
    if not template:
        ws.row_dimensions[r].height = height
    return r + 1


def _blank_workbook():
    return openpyxl.Workbook()


def _template_sheet(wb, name: str):
    """自定义模板里同名工作表，数据区从第 4 行开始（保留标题与表头样式）。"""
    if name not in wb.sheetnames:
        return None
    ws = wb[name]
    for row in ws.iter_rows(min_row=4):
        for cell in row:
            cell.value = None
    return ws


def _fill_box_cards_detail_sheet(ws, title: str, subtitle: str, boxes: list[Any], circuits: list[Any], components: list[Any], target_brand: str = "", sheet_title: str = "") -> dict[str, int]:
    """生成按箱体垂直流水卡片展开的分项明细表，返回每个箱体卡片起始行号以供汇总表做超链接跳转。"""
    ws.title = sheet_title or _SHEETS["box_cards"]
    card_anchors: dict[str, int] = {}

    # 全局总标题
    ws.merge_cells("A1:J1")
    c = ws.cell(row=1, column=1, value=f"{title}——成套箱体分项卡片明细表")
    c.font = TITLE_FONT
    c.alignment = CENTER
    ws.row_dimensions[1].height = 32

    ws.merge_cells("A2:J2")
    c = ws.cell(row=2, column=1, value=subtitle)
    c.font = SUB_FONT
    c.alignment = CENTER
    ws.row_dimensions[2].height = 22

    # 列宽设定
    widths = _COLUMNS["box_cards"]
    for j, w in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(j)].width = w

    # 将回路与器件按箱体归类
    circuits_by_box: dict[str, list[Any]] = {}
    for c in circuits:
        b_code = getattr(c, "box", "") if hasattr(c, "box") else str(c.get("box", ""))
        circuits_by_box.setdefault(b_code, []).append(c)

    comps_by_box = _build_comps_by_box(components)

    r = 4
    for b_idx, b in enumerate(boxes, start=1):
        code = getattr(b, "code", "") if hasattr(b, "code") else str(b.get("code", "未命名"))
        name = getattr(b, "name", "") if hasattr(b, "name") else str(b.get("name", "配电箱"))
        size = getattr(b, "size", "") if hasattr(b, "size") else str(b.get("size", "-"))
        loc = getattr(b, "location", "") if hasattr(b, "location") else str(b.get("location", "-"))
        install = getattr(b, "install", "") if hasattr(b, "install") else str(b.get("install", "-"))
        qty = getattr(b, "quantity", 1) if hasattr(b, "quantity") else (b.get("quantity", 1) or 1)
        qty_num = int(qty) if float(qty) == int(qty) else qty

        b_circuits = circuits_by_box.get(code, [])
        b_comps = comps_by_box.get(code, [])
        costs = _estimate_box_costs(b, b_circuits, b_comps, brand=target_brand or DEFAULT_TARGET_BRAND)

        # 记录超链接锚点行
        card_anchors[code] = r

        # 1. 卡片主横幅（深蓝背景，加粗白字）
        ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=10)
        banner_text = f"【箱柜编号：{code}】 设备名称：{name}  ｜  型号规格：{size}  ｜  安装使用部位：{loc}  ｜  安装方式：{install}  ｜  台数：{qty_num} 台"
        cell_banner = ws.cell(row=r, column=1, value=banner_text)
        cell_banner.font = WHITE_BOLD_FONT
        cell_banner.fill = CARD_HDR_FILL
        cell_banner.alignment = Alignment(horizontal="left", vertical="center", indent=1)
        ws.row_dimensions[r].height = 28
        r += 1

        # 2. 价格构成小计条（浅蓝背景）
        ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=10)
        cost_text = (
            f"  成套造价测算构成：箱体外壳 ¥{costs['box_shell']:.2f} ｜ "
            f"元器件总额 ¥{costs['comp_total']:.2f} ｜ "
            f"辅材母排 ¥{costs['bus_total']:.2f} ｜ "
            f"安装调试试验 ¥{costs['labor_total']:.2f} ｜ "
            f"单台合价：¥{costs['unit_price']:.2f} ｜ "
            f"总合价：¥{costs['unit_price'] * float(qty_num):.2f}"
        )
        cell_cost = ws.cell(row=r, column=1, value=cost_text)
        cell_cost.font = DARK_BOLD_FONT
        cell_cost.fill = CARD_SUB_FILL
        cell_cost.alignment = Alignment(horizontal="left", vertical="center", indent=1)
        ws.row_dimensions[r].height = 24
        r += 1

        # 3. 回路表头
        sub_headers = ["序号", "回路编号", "回路用途/负荷名称", "器件类别", "开关/断路器规格", "接触器/附件", "设备容量(kW)", "相序", "导线型号及敷设", "工程备注"]
        for j, sh in enumerate(sub_headers, start=1):
            sc = ws.cell(row=r, column=j, value=sh)
            sc.font = SUMMARY_HDR_FONT
            sc.fill = SUMMARY_HDR_FILL
            sc.alignment = CENTER
            sc.border = DARK_BORDER
        ws.row_dimensions[r].height = 24
        r += 1

        # 4. 回路列表
        if b_circuits:
            for c_i, cir in enumerate(b_circuits, start=1):
                cir_no = getattr(cir, "circuit_no", "") if hasattr(cir, "circuit_no") else str(cir.get("circuit_no", ""))
                load_name = getattr(cir, "load_name", "") if hasattr(cir, "load_name") else str(cir.get("load_name", ""))
                breaker = getattr(cir, "breaker", "") if hasattr(cir, "breaker") else str(cir.get("breaker", ""))
                contactor = getattr(cir, "contactor", "") if hasattr(cir, "contactor") else str(cir.get("contactor", "-"))
                power_kw = getattr(cir, "power_kw", "") if hasattr(cir, "power_kw") else str(cir.get("power_kw", ""))
                phase = getattr(cir, "phase", "") if hasattr(cir, "phase") else str(cir.get("phase", ""))
                cable = getattr(cir, "cable", "") if hasattr(cir, "cable") else str(cir.get("cable", ""))
                note = getattr(cir, "note", "") if hasattr(cir, "note") else str(cir.get("note", ""))

                row_vals = [c_i, cir_no, load_name, "微断/塑壳", breaker, contactor, power_kw, phase, cable, note]
                for j, v in enumerate(row_vals, start=1):
                    cell = ws.cell(row=r, column=j, value=sanitize_excel_value(v))
                    cell.font = CELL_FONT
                    cell.alignment = CENTER if j in (1, 2, 4, 7, 8) else LEFT
                    cell.border = BORDER
                ws.row_dimensions[r].height = 22
                r += 1
        else:
            ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=10)
            c_empty = ws.cell(row=r, column=1, value="（本箱体图纸未单列支路回路，详见成套总装说明与配置）")
            c_empty.font = SUB_FONT
            c_empty.alignment = CENTER
            c_empty.border = BORDER
            ws.row_dimensions[r].height = 24
            r += 1

        # 5. 箱体附属配置元器件（若有）
        if b_comps:
            comp_names = [
                f"{getattr(cp, 'name', '') if hasattr(cp, 'name') else cp.get('name', '')} "
                f"({getattr(cp, 'spec', '') if hasattr(cp, 'spec') else cp.get('spec', '')} × "
                f"{getattr(cp, 'quantity', 1) if hasattr(cp, 'quantity') else cp.get('quantity', 1)})"
                for cp in b_comps
            ]
            ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=10)
            cp_cell = ws.cell(row=r, column=1, value=f"  附加装置与非回路器件：{'；'.join(comp_names)}")
            cp_cell.font = Font(name=_FONT_FAMILY, size=9, color=_PALETTE["cover_company_text"])
            cp_cell.alignment = Alignment(horizontal="left", vertical="center")
            cp_cell.fill = PatternFill("solid", fgColor=_PALETTE["card_alt_fill"])
            cp_cell.border = BORDER
            ws.row_dimensions[r].height = 22
            r += 1

        # 每个箱体卡片留 2 行空白隔离
        r += 2

    return card_anchors


def _fill_quotation_summary_sheet(ws, project_title: str, boxes: list[Any], circuits: list[Any], components: list[Any], card_anchors: dict[str, int], detail_sheet_title: str = _SHEETS["box_cards"], target_brand: str = "", sheet_title: str = ""):
    """高标准复刻成套设备报价(汇总)报表：
    表头包含项目单位、项目名称、联系人/电话、金额单位，配电箱绿色分类条，
    序号支持 Excel 原生超链接跳转直达箱体分项流水卡片，数量、单价、总价公式与底部自动求和。
    """
    ws.title = sheet_title or "成套设备报价(汇总)"

    headers = ["序号", "柜号", "箱柜名称", "箱柜型号", "单位", "数量", "单价", "总价", "备注"]
    col_widths = _COLUMNS["cover_legacy"]
    for j, w in enumerate(col_widths, start=1):
        ws.column_dimensions[get_column_letter(j)].width = w

    # 第 1 行：居中大标题
    ws.merge_cells("A1:I1")
    title_cell = ws.cell(row=1, column=1, value="成套设备报价(汇总)")
    title_cell.font = SUMMARY_TITLE_FONT
    title_cell.alignment = CENTER
    ws.row_dimensions[1].height = 36

    # 第 2 行：空行
    ws.row_dimensions[2].height = 10

    # 第 3 行：项目单位
    ws.cell(row=3, column=1, value="项目单位：").font = META_FONT
    ws.row_dimensions[3].height = 20

    # 第 4 行：项目名称
    clean_proj_name = project_title.replace("——成套箱体分项卡片明细表", "").replace("图纸扒图_", "")
    ws.cell(row=4, column=1, value=f"项目名称：{clean_proj_name}").font = META_FONT
    ws.row_dimensions[4].height = 20

    # 第 5 行：联系人、联系电话、金额单位
    ws.cell(row=5, column=1, value="联系人：                          联系电话：").font = META_FONT
    c_unit = ws.cell(row=5, column=9, value="金额单位：人民币元")
    c_unit.font = META_FONT
    c_unit.alignment = RIGHT
    ws.row_dimensions[5].height = 20

    # 第 6 行：配电箱分类条目（浅绿底色，整行贯通）
    ws.merge_cells("A6:I6")
    cat_cell = ws.cell(row=6, column=1, value="配电箱")
    cat_cell.font = CATEGORY_FONT
    cat_cell.fill = CATEGORY_FILL
    cat_cell.alignment = Alignment(horizontal="left", vertical="center", indent=1)
    for col in range(1, 10):
        ws.cell(row=6, column=col).border = DARK_BORDER
    ws.row_dimensions[6].height = 24

    # 第 7 行：表头
    for j, h in enumerate(headers, start=1):
        hc = ws.cell(row=7, column=j, value=h)
        hc.font = SUMMARY_HDR_FONT
        hc.fill = SUMMARY_HDR_FILL
        hc.alignment = CENTER
        hc.border = DARK_BORDER
    ws.row_dimensions[7].height = 28

    # 循环写入各箱体
    circuits_by_box: dict[str, list[Any]] = {}
    for c in circuits:
        b_code = getattr(c, "box", "") if hasattr(c, "box") else str(c.get("box", ""))
        circuits_by_box.setdefault(b_code, []).append(c)

    comps_by_box = _build_comps_by_box(components)

    start_data_row = 8
    curr_row = start_data_row

    for i, b in enumerate(boxes, start=1):
        code = getattr(b, "code", "") if hasattr(b, "code") else str(b.get("code", "未命名"))
        name = getattr(b, "name", "") if hasattr(b, "name") else str(b.get("name", "配电箱"))
        size = getattr(b, "size", "") if hasattr(b, "size") else str(b.get("size", ""))
        loc = getattr(b, "location", "") if hasattr(b, "location") else str(b.get("location", ""))
        qty = getattr(b, "quantity", 1) if hasattr(b, "quantity") else (b.get("quantity", 1) or 1)
        qty_num = int(qty) if float(qty) == int(qty) else qty

        # 箱柜型号规范显示：缺失时标"待确认"，不编造型号
        box_model = size if size and size != "-" else "待确认"

        b_circuits = circuits_by_box.get(code, [])
        b_comps = comps_by_box.get(code, [])
        costs = _estimate_box_costs(b, b_circuits, b_comps, brand=target_brand or DEFAULT_TARGET_BRAND)

        # 序号：从 1 开始自然编号，带超链接跳转到分项明细卡片
        seq_num = i
        c_seq = ws.cell(row=curr_row, column=1, value=seq_num)
        c_seq.alignment = CENTER
        c_seq.border = DARK_BORDER

        card_target_row = card_anchors.get(code)
        if card_target_row:
            c_seq.hyperlink = f"#'{detail_sheet_title}'!A{card_target_row}"
            c_seq.font = LINK_FONT
        else:
            c_seq.font = CELL_FONT

        # 柜号
        c_code = ws.cell(row=curr_row, column=2, value=code)
        c_code.font = CELL_FONT
        c_code.alignment = LEFT
        c_code.border = DARK_BORDER

        # 箱柜名称
        c_name = ws.cell(row=curr_row, column=3, value=name)
        c_name.font = CELL_FONT
        c_name.alignment = LEFT
        c_name.border = DARK_BORDER

        # 箱柜型号
        c_model = ws.cell(row=curr_row, column=4, value=box_model)
        c_model.font = CELL_FONT
        c_model.alignment = LEFT
        c_model.border = DARK_BORDER

        # 单位
        c_unit_cell = ws.cell(row=curr_row, column=5, value=_ESTIMATION["default_box_unit"])
        c_unit_cell.font = CELL_FONT
        c_unit_cell.alignment = CENTER
        c_unit_cell.border = DARK_BORDER

        # 数量
        c_qty = ws.cell(row=curr_row, column=6, value=qty_num)
        c_qty.font = CELL_FONT
        c_qty.alignment = CENTER
        c_qty.border = DARK_BORDER

        # 单价
        c_price = ws.cell(row=curr_row, column=7, value=costs["unit_price"])
        c_price.font = CELL_FONT
        c_price.number_format = "#,##0.00"
        c_price.alignment = RIGHT
        c_price.border = DARK_BORDER

        # 总价公式
        c_total = ws.cell(row=curr_row, column=8, value=f"=F{curr_row}*G{curr_row}")
        c_total.font = CELL_FONT
        c_total.number_format = "#,##0.00"
        c_total.alignment = RIGHT
        c_total.border = DARK_BORDER

        # 备注（使用部位/所属楼栋车间，宁可标疑、绝不臆造具体房间）
        loc_val = str(loc).strip() if loc and str(loc).strip() and str(loc).strip() != "-" else "待确认"
        c_loc = ws.cell(row=curr_row, column=9, value=loc_val)
        c_loc.font = CELL_FONT
        c_loc.alignment = LEFT
        c_loc.border = DARK_BORDER

        ws.row_dimensions[curr_row].height = 24
        curr_row += 1

    # 底部合计行
    last_data_row = curr_row - 1
    ws.merge_cells(start_row=curr_row, start_column=1, end_row=curr_row, end_column=5)
    tot_label = ws.cell(row=curr_row, column=1, value="合  计")
    tot_label.font = TOTAL_FONT
    tot_label.alignment = CENTER
    tot_label.fill = TOTAL_FILL

    for c_i in range(1, 6):
        ws.cell(row=curr_row, column=c_i).border = DOUBLE_BOTTOM_BORDER
        ws.cell(row=curr_row, column=c_i).fill = TOTAL_FILL

    # 数量合计
    tot_qty = ws.cell(row=curr_row, column=6, value=f"=SUM(F{start_data_row}:F{last_data_row})")
    tot_qty.font = TOTAL_FONT
    tot_qty.alignment = CENTER
    tot_qty.fill = TOTAL_FILL
    tot_qty.border = DOUBLE_BOTTOM_BORDER

    # 单价空
    tot_dash = ws.cell(row=curr_row, column=7, value="-")
    tot_dash.font = TOTAL_FONT
    tot_dash.alignment = CENTER
    tot_dash.fill = TOTAL_FILL
    tot_dash.border = DOUBLE_BOTTOM_BORDER

    # 总价合计
    tot_price = ws.cell(row=curr_row, column=8, value=f"=SUM(H{start_data_row}:H{last_data_row})")
    tot_price.font = TOTAL_FONT
    tot_price.number_format = "#,##0.00"
    tot_price.alignment = RIGHT
    tot_price.fill = TOTAL_FILL
    tot_price.border = DOUBLE_BOTTOM_BORDER

    # 备注空
    tot_rem = ws.cell(row=curr_row, column=9, value="")
    tot_rem.fill = TOTAL_FILL
    tot_rem.border = DOUBLE_BOTTOM_BORDER

    ws.row_dimensions[curr_row].height = 28


def _clean_proj_title(raw_title: str) -> str:
    cleaned = (raw_title or "").replace("——" + _LABELS["detail_card_suffix"], "").replace("图纸扒图_", "").replace(BASE_TITLE, "").strip()
    if not cleaned or cleaned == "未命名项目":
        return ""  # 宁可留空、不许编造：不再回退到具体项目名
    return cleaned


def _result_box_items(box, b_circuits: list[Any], b_comps: list[Any], target_brand: str) -> list[dict]:
    """图纸结果 → 报价明细行。缺型号时如实写“待确认”、单价计 0，不编造型号与价格。"""
    from .pricing import calculate_component_unit_price, estimate_box_enclosure_price
    from .catalog import identify_brand

    code = _val(box, "code", "未命名")
    size = _val(box, "size", "")
    items: list[dict] = []

    def device_name(spec: str) -> str:
        up = spec.upper()
        if any(k in up for k in ["LE", "VM", "RCBO", "ELE", "30MA", "漏电"]):
            return "微型漏电断路器"
        if any(k in up for k in ["MCCB", "NM", "NSX", "160A", "250A"]):
            return "塑壳断路器"
        return "微型断路器"

    def add(name: str, spec: str, unit: str = "只", qty: int = 1, note: str = "") -> None:
        if not spec or spec == "-":
            items.append({"name": f"{name}（待确认）", "spec": "待确认", "unit": unit,
                          "qty": qty, "unit_price": 0.0, "brand": "待确认",
                          "note": "待补充规格型号"})
            return
        spec_brand = identify_brand(spec)
        use_brand = spec_brand if spec_brand != GENERIC_BRAND else (target_brand or DEFAULT_TARGET_BRAND)
        unit_price, source, basis = calculate_component_unit_price(spec, brand=use_brand)
        items.append({"name": name, "spec": spec, "unit": unit, "qty": qty,
                      "unit_price": round(unit_price, 2), "brand": use_brand,
                      "note": basis if source != "EXACT" else note})

    # 进线主控器件
    incomer = None
    for c in b_circuits:
        c_no, c_load = _val(c, "circuit_no", ""), _val(c, "load_name", "")
        if "进线" in c_no or "进线" in c_load:
            incomer = c
            break
    incomer_spec = _val(incomer, "breaker", "") if incomer else ""
    incomer_name = "微型隔离开关" if "SW" in incomer_spec or "隔离" in incomer_spec else device_name(incomer_spec or " ")
    add(incomer_name, incomer_spec)

    # 出线分支断路器
    for cir in b_circuits:
        c_no, c_load = _val(cir, "circuit_no", ""), _val(cir, "load_name", "")
        if "进线" in c_no or "进线" in c_load:
            continue
        spec = _val(cir, "breaker", "")
        add(device_name(spec or " "), spec)

    # 电涌保护器：只写图纸真实检出的那一项，检不到就不加行（不系统性地补项）
    for cp in b_comps:
        spec = _val(cp, "spec", "")
        if any(k in spec.upper() or k in spec for k in ["SPD", "浪涌", "防雷", "避雷"]):
            add("电涌保护器", spec)
            break

    # 壳体（钣金喷塑箱体）
    b_dict = box.model_dump() if hasattr(box, "model_dump") else (box if isinstance(box, dict) else vars(box))
    enclosure_price, enc_basis = estimate_box_enclosure_price(b_dict, len(b_circuits))
    items.append({"name": "壳体", "spec": size if size and size != "-" else "标准配电箱外壳",
                  "unit": "台", "qty": 1, "unit_price": round(enclosure_price, 2),
                  "brand": "成套定制", "note": enc_basis or ""})

    return items


def _quote_model_base(project: str, owner: str = "") -> dict:
    """组价模型基座：只有项目与金额口径，没有报价单位/日期/联系人（AI 组价不是供应商报价）。"""
    qt = _DELIVERY.get("quote_template") or {}
    return {
        "project": _clean_proj_title(project),
        "owner": owner,
        "currency": "人民币元",
        "category": _SHEETS.get("category", "配电箱"),
        "footer_note": qt.get("footer_note", ""),
        "quote_by": "",
        "review_by": "",
        "boxes": [],
        "unresolved_count": 0,
    }


def quote_model_from_result(result: ExtractionResult, target_brand: str = "",
                            owner: str = "") -> dict:
    """图纸提取结果 → 报价模型（自动出单用；费率走配置，不写死在代码里）。"""
    circuits_by_box: dict[str, list[Any]] = {}
    for c in result.circuits or []:
        circuits_by_box.setdefault(_val(c, "box", ""), []).append(c)
    comps_by_box = _build_comps_by_box(result.components or [])

    model = _quote_model_base(result.title, owner)
    model["unresolved_count"] = len([u for u in (result.uncertainties or [])
                                     if not getattr(u, "resolved", False)])
    for b in result.boxes or []:
        code = _val(b, "code", "未命名")
        b_circuits = circuits_by_box.get(code, [])
        items = _result_box_items(b, b_circuits, comps_by_box.get(code, []), target_brand)
        qty = _val(b, "quantity", 1) or 1
        cost = quote_box(items, qty)
        model["boxes"].append({
            "code": code,
            "name": _val(b, "name", "配电箱"),
            "model": (_val(b, "size", "") or "").strip() if _val(b, "size", "") not in ("-", None) else "",
            "location": _val(b, "location", "") or "待确认",
            "unit": "台",
            "quantity": qty,
            "items": items,
            "subtotal": cost["subtotal"],
            "auxiliary": cost["auxiliary"],
            "manufacture": cost["manufacture"],
            "tax_total": cost["tax"],
            "unit_price": cost["unit_total"],
            "total_price": cost["total"],
        })
    _finalize_model(model)
    return model


def quote_model_from_quote_res(quote_res: dict, job_info: dict, target_brand: str = "") -> dict:
    """组价工作台的核算结果 → 报价模型（含用户手改价、平替品牌与费率联动）。"""
    info = job_info or {}
    project = info.get("project") or (info.get("summary") or {}).get("title") or ""
    model = _quote_model_base(project)

    rows = quote_res.get("rows") or []
    boxes = {b.get("box_code"): b for b in quote_res.get("boxes") or []}
    groups: list[dict] = []
    current: dict | None = None
    for row in rows:
        if row.get("type") == "box_header":
            current = {"header": row, "items": []}
            groups.append(current)
        elif row.get("type") in ("component", "material") and current is not None:
            current["items"].append(row)

    for group in groups:
        header = group["header"]
        code = header.get("box_code") or ""
        b_info = boxes.get(code) or {}
        items = [{
            "name": row.get("name", ""),
            "spec": row.get("replacement_model") or row.get("design_spec") or "",
            "unit": row.get("unit") or "只",
            "qty": int(row.get("quantity") or 1),
            "unit_price": float(row.get("unit_price") or 0.0),
            "brand": row.get("brand") or "",
            "note": row.get("source") if row.get("source") not in (None, "平替") else "",
        } for row in group["items"]]

        qty = header.get("quantity") or b_info.get("quantity") or 1
        breakdown = b_info.get("breakdown") or quote_box(items, qty)
        model["boxes"].append({
            "code": code,
            "name": header.get("name") or b_info.get("name") or "配电箱",
            "model": b_info.get("size") or "",
            "location": b_info.get("location") or header.get("location") or "",
            "unit": "台",
            "quantity": qty,
            "items": items,
            "subtotal": float(breakdown.get("subtotal") or 0.0),
            "auxiliary": float(breakdown.get("auxiliary") or 0.0),
            "manufacture": float(breakdown.get("manufacture") or 0.0),
            "tax_total": float(breakdown.get("tax") or 0.0),
            "unit_price": float(breakdown.get("unit_total") or 0.0),
            "total_price": float(breakdown.get("total") or 0.0),
        })
    _finalize_model(model)
    return model


def _finalize_model(model: dict) -> None:
    model["total_qty"] = sum(int(b.get("quantity") or 1) for b in model["boxes"])
    model["total_price"] = round(sum(float(b.get("total_price") or 0.0) for b in model["boxes"]), 2)
    project = quote_project(model["boxes"])
    model["grand_total"] = project["grand_total"]
    model["total_upper"] = project["upper"]


def _fill_quote_template(wb, model: dict):
    from . import quote_template
    return quote_template.fill(wb, model)



def _fill_sheets(wb, result, subtitle, template: bool, target_brand: str = ""):
    """构建成套设备高精度多级报表：
    Sheet 1: 成套设备报价(汇总) —— 截图同款汇总表头与超链接直达；
    Sheet 2: 箱体分项明细 —— 垂直流水卡片展开；
    Sheet 3: 箱体清单 —— 原始参数台账；
    Sheet 4: 元器件汇总 —— 全图元器件汇总；
    Sheet 5: 回路明细 —— 完整回路参数；
    Sheet 6: 技术要求与报价说明。
    """
    if template:
        # 自定义模板导出处理
        ws1 = _template_sheet(wb, "箱体清单") or wb.active
        r = 4
        for i, b in enumerate(result.boxes, 1):
            r = _row(ws1, r, [i, b.code, b.name, b.ip_rating, b.install, b.location,
                              b.size, b.quantity, b.note], template=True)

        ws2 = _template_sheet(wb, _SHEETS["components"]) or wb.create_sheet(_SHEETS["components"])
        r = 4
        for i, c in enumerate(result.components, 1):
            q = int(c.quantity) if float(c.quantity) == int(c.quantity) else c.quantity
            r = _row(ws2, r, [i, c.name, c.spec, c.unit, q, c.used_in, c.note], template=True)

        ws3 = _template_sheet(wb, _SHEETS["circuits"]) or wb.create_sheet(_SHEETS["circuits"])
        r = 4
        for i, c in enumerate(result.circuits, 1):
            r = _row(ws3, r, [i, c.box, c.phase, c.breaker, c.contactor, c.ct, c.thermal,
                              c.power_kw, c.circuit_no, c.cable, c.current_a,
                              c.load_name, c.secondary_ref, c.start_method, c.note],
                     template=True, height=44, center_cols=(1, 2, 3))
        return

    # 1. 创建 Sheet 1 (成套设备报价汇总) 与 Sheet 2 (箱体分项明细卡片)
    ws_summary = wb.active
    ws_cards = wb.create_sheet(_SHEETS["box_cards"])

    # 先渲染卡片明细，获取每个箱体的超链接行号
    card_anchors = _fill_box_cards_detail_sheet(
        ws_cards, result.title, subtitle,
        result.boxes, result.circuits, result.components,
        target_brand=target_brand or DEFAULT_TARGET_BRAND
    )

    # 渲染第一页成套报价汇总表
    _fill_quotation_summary_sheet(
        ws_summary, result.title,
        result.boxes, result.circuits, result.components,
        card_anchors, detail_sheet_title=_SHEETS["box_cards"],
        target_brand=target_brand or DEFAULT_TARGET_BRAND
    )

    _fill_appendix_sheets(wb, result, subtitle)


def _fill_appendix_sheets(wb, result: ExtractionResult, subtitle: str) -> None:
    """报价表之后的附录：箱体清单、元器件汇总、回路明细、技术要求。

    两张报价表是交给客户的，附录是留给自己核对的：版式不被附录打乱，信息也不丢。
    没有内容的附录不建空表，避免客户打开时看到一堆空页。
    """
    if result.boxes:
        ws_box = wb.create_sheet(_SHEETS["boxes"])
        headers_box = ["序号", "设备编号", "设备名称", "防护等级", "安装方式", "安装位置", "参考尺寸", "数量(台)", "备注"]
        r_box = _setup(ws_box, result.title, subtitle, headers_box, _COLUMNS["boxes"])
        for i, b in enumerate(result.boxes, 1):
            r_box = _row(ws_box, r_box, [i, b.code, b.name, b.ip_rating, b.install, b.location,
                                         b.size, b.quantity, b.note])

    if result.components:
        ws_comp = wb.create_sheet(_SHEETS["components"])
        headers_comp = ["序号", "元器件名称", "规格型号", "单位", "数量", "用于箱体/回路", "备注"]
        r_comp = _setup(ws_comp, result.title, subtitle, headers_comp, _COLUMNS["components"])
        for i, c in enumerate(result.components, 1):
            q = int(c.quantity) if float(c.quantity) == int(c.quantity) else c.quantity
            r_comp = _row(ws_comp, r_comp, [i, c.name, c.spec, c.unit, q, c.used_in, c.note])

    if result.circuits:
        ws_cir = wb.create_sheet(_SHEETS["circuits"])
        headers_cir = ["序号", "箱体编号", "相序", "断路器", "接触器", "电流互感器", "热继电器",
                       "设备容量(kW)", "回路编号", "导线型号及敷设", "计算电流(A)",
                       "回路名称", "二次图编号", "启动方式", "备注"]
        r_cir = _setup(ws_cir, result.title, subtitle, headers_cir, _COLUMNS["circuits"])
        for i, c in enumerate(result.circuits, 1):
            r_cir = _row(ws_cir, r_cir, [i, c.box, c.phase, c.breaker, c.contactor, c.ct, c.thermal,
                                        c.power_kw, c.circuit_no, c.cable, c.current_a,
                                        c.load_name, c.secondary_ref, c.start_method, c.note],
                         height=44, center_cols=(1, 2, 3))

    reqs = list(result.requirements)
    observed = uncertainty_texts(result, "model") + [
        u.text for u in result.uncertainties if not u.source and u.text]
    if observed:
        reqs.append({"item": "待人工核对项", "content": "；".join(observed)})
    warnings = uncertainty_texts(result, "program")
    if warnings:
        reqs.append({"item": "程序核对告警", "content": "；".join(warnings)})
    if reqs:
        ws_req = wb.create_sheet(_SHEETS["requirements"])
        r_req = _setup(ws_req, result.title, subtitle, ["序号", "项目", "要求内容"], _COLUMNS["requirements"])
        for i, q in enumerate(reqs, 1):
            item = q.item if hasattr(q, "item") else q["item"]
            content = q.content if hasattr(q, "content") else q["content"]
            r_req = _row(ws_req, r_req, [i, item, content], height=64, center_cols=(1,))


def _fill_replacements(wb, components: list, target_brand: str = ""):
    """国产化平替方案：对标外资/国标物料并推荐一线国产品牌对等型号与降本测算。"""
    from .catalog import analyze_components_replacement

    comp_dicts = []
    for c in components:
        if hasattr(c, "model_dump"):
            comp_dicts.append(c.model_dump())
        elif isinstance(c, dict):
            comp_dicts.append(c)

    if not comp_dicts:
        return

    analysis = analyze_components_replacement(comp_dicts, target_brand=target_brand)
    sheet_name = f"国产化平替方案({target_brand})"
    if sheet_name in wb.sheetnames:
        del wb[sheet_name]
    ws = wb.create_sheet(sheet_name)

    headers = [
        "序号", "元器件名称", "原图设计规格", "原厂品牌",
        f"推荐对标型号({target_brand})", "采购数量", "单位",
        "预计降本", "电气性能对标与核验说明"
    ]
    subtitle = (
        f"平替目标品牌：{target_brand} ｜ 涉及物料：{analysis['total_components']} 项 / {analysis['total_quantity']} 件 "
        f"｜ 具备平替降本空间：{analysis['replaceable_quantity']} 件 ｜ 预计整体采购降本幅度：约 {analysis['estimated_overall_saving_pct']}%"
    )
    r = _setup(ws, f"电气元器件国产化智能平替与成本对账表（{target_brand}）", subtitle, headers,
               [6, 20, 28, 12, 34, 10, 8, 12, 45])

    save_font = Font(name=_FONT_FAMILY, size=10, bold=True, color=_PALETTE["saving_text"])
    warn_font = Font(name=_FONT_FAMILY, size=10, bold=True, color=_PALETTE["warning_text"])
    for i, it in enumerate(analysis["items"], 1):
        sav_pct = it.get("estimated_saving_pct")
        sav_status = it.get("saving_status")
        if sav_pct is not None and sav_pct > 0:
            saving_text = f"↓{sav_pct}%"
        elif sav_status == "cost_increase":
            saving_text = "成本增加"
        elif sav_status == "cost_equal":
            saving_text = "平价"
        elif sav_pct is None or sav_status == "missing_price":
            saving_text = "待询价"
        else:
            saving_text = "已最优"

        curr_r = r
        r = _row(ws, r, [
            i, it["name"], it["original_spec"], it["original_brand"],
            it["recommended_model"], it["quantity"], it["unit"],
            saving_text, it["notes"]
        ], height=32, center_cols=(1, 4, 7, 8))
        if sav_pct is not None and sav_pct > 0:
            ws.cell(row=curr_r, column=8).font = save_font
        elif sav_status == "cost_increase":
            ws.cell(row=curr_r, column=8).font = warn_font


def _fill_changes(wb, changes):
    """变更记录：人工/语音/AI 改动逐条留痕，随清单一起交付，便于对报价做追溯。"""
    if "变更记录" in wb.sheetnames:
        del wb["变更记录"]
    ws = wb.create_sheet(_SHEETS["changes"])
    headers = ["序号", "时间", "来源", "对象", "字段", "原值", "新值", "说明"]
    r = _setup(ws, "人工修改与 AI 修改记录", "数据来自工作台编辑留痕，可用于报价追溯",
               headers, [6, 20, 12, 18, 12, 30, 30, 28])
    for i, entry in enumerate(changes or [], 1):
        r = _row(ws, r, [i, entry.get("ts", ""), entry.get("source", ""),
                         entry.get("target", ""), entry.get("field", ""),
                         entry.get("old", ""), entry.get("new", ""),
                         entry.get("reason", "")], height=24)


def _fill_topology_sheet(wb, topology: list, title: str, subtitle: str):
    """绘制配电系统层级拓扑树工作表，支持直观展示总柜 -> 分箱 -> 二次原理图挂接关系。"""
    if not topology:
        return
    sheet_name = "配电系统拓扑树"
    if sheet_name in wb.sheetnames:
        del wb[sheet_name]
    ws = wb.create_sheet(sheet_name)
    headers = ["序号", "系统层级拓扑架构", "节点类别", "设备/图号", "设备名称/回路描述", "供电上级柜", "供电回路", "设备容量", "回路数", "二次图号", "工程备注/规格"]
    r = _setup(ws, title, subtitle, headers, _COLUMNS["detail"])

    type_names = {
        "cabinet": "一级总柜",
        "box": "二级分箱",
        "secondary": "二次控制",
        "circuit": "出线支路",
    }
    cab_fill = PatternFill("solid", fgColor=_PALETTE["cabinet_fill"])
    sec_font = Font(name=_FONT_FAMILY, size=10, italic=True, color=_PALETTE["border_dark"])
    bold_font = Font(name=_FONT_FAMILY, size=10, bold=True)

    flat_items = []

    def _flatten(nodes, indent=0):
        for n in nodes:
            t = getattr(n, "node_type", "box")
            code = getattr(n, "code", "")
            t_label = type_names.get(t, "配电箱")
            if indent == 0:
                tree_label = f"【{t_label}】{code}"
            else:
                tree_label = f"{'    ' * indent}└── 【{t_label}】{code}"
            flat_items.append((n, tree_label, indent))
            _flatten(getattr(n, "children", []) or [], indent + 1)

    _flatten(topology)

    for i, (n, tree_label, indent) in enumerate(flat_items, 1):
        t = getattr(n, "node_type", "box")
        t_label = type_names.get(t, "配电箱")
        c_cnt = getattr(n, "circuits_count", 0)
        c_cnt_val = c_cnt if c_cnt > 0 else "-"
        curr_row = r
        r = _row(ws, r, [
            i, tree_label, t_label, getattr(n, "code", ""),
            getattr(n, "name", ""), getattr(n, "parent_code", "") or "-",
            getattr(n, "feed_circuit", "") or "-", getattr(n, "power_kw", "") or "-",
            c_cnt_val, getattr(n, "secondary_ref", "") or "-", getattr(n, "note", "")
        ], height=28, center_cols=(1, 3, 4, 6, 7, 8, 9, 10))

        if t == "cabinet":
            for col in range(1, len(headers) + 1):
                ws.cell(row=curr_row, column=col).fill = cab_fill
            ws.cell(row=curr_row, column=2).font = bold_font
        elif t == "secondary":
            ws.cell(row=curr_row, column=2).font = sec_font


def _fill_reconciliation_sheet(wb, reconciliation, title: str, subtitle: str):
    """图纸目录与提取覆盖对账审计表导出。"""
    if not reconciliation or not getattr(reconciliation, "has_catalog", False):
        return
    sheet_name = "图纸目录对账审计"
    if sheet_name in wb.sheetnames:
        del wb[sheet_name]
    ws = wb.create_sheet(sheet_name)
    headers = ["序号", "图纸编号", "图纸名称", "目录声明箱柜", "实际覆盖提取箱柜", "缺失未提取箱柜", "对账状态", "声明类型", "缺失分类"]
    sub = (
        f"目录声明总数: {reconciliation.total_declared_panels} 台 ｜ 实际覆盖提取: {reconciliation.covered_count} 台 "
        f"｜ 缺失未提取: {reconciliation.missing_count} 台 ｜ 覆盖率: {int(reconciliation.coverage_rate * 100)}%"
        f"｜ 系统图漏单元: {getattr(reconciliation, 'system_unit_missing_count', 0)} "
        f"｜ 平面/表栏声明: {getattr(reconciliation, 'plan_or_schedule_declared_count', 0)}"
    )
    r = _setup(ws, f"{title}——图纸目录对账审计表", sub, headers, _COLUMNS["reconciliation"])
    items = getattr(reconciliation, "items", []) or []
    for i, item in enumerate(items, 1):
        status_text = "全部覆盖" if item.status == "COVERED" else ("整张图幅缺失" if item.status == "MISSING" else "部分覆盖")
        kind_text = {
            "sheet_catalog": "图纸目录",
            "range_annotation": "平面/表栏范围",
            "caption": "箱名声明",
        }.get(getattr(item, "declaration_kind", "") or "", getattr(item, "declaration_kind", "") or "-")
        deficit_text = {
            "system_unit_missing": "系统图漏单元",
            "plan_or_schedule_declared": "平面/表栏声明",
        }.get(getattr(item, "deficit_kind", "") or "", getattr(item, "deficit_kind", "") or ("-" if item.status == "COVERED" else "-"))
        r = _row(
            ws, r,
            [
                i,
                item.sheet_no,
                item.sheet_title,
                "、".join(item.declared_panels) if item.declared_panels else "(无声明)",
                "、".join(item.matched_panels) if item.matched_panels else "(无覆盖)",
                "、".join(item.missing_panels) if item.missing_panels else "(无缺失)",
                status_text,
                kind_text,
                deficit_text,
            ],
            height=32,
            center_cols=(1, 2, 7, 8, 9)
        )


def _fill_dual_sheets(wb, result: ExtractionResult, subtitle: str, target_brand: str = ""):
    """生成用户指定的极简双工作表标准报价表：
    Sheet 1: 配电箱柜汇总表 —— 全项目所有箱体汇总台账与报价
    Sheet 2: 箱柜及元器件明细 —— 每个箱体上方为箱体基本信息横幅，下方为回路与元器件明细清单，多箱体垂直流水排列
    """
    ws_summary = wb.active
    ws_detail = wb.create_sheet("箱柜及元器件明细")

    card_anchors = _fill_box_cards_detail_sheet(
        ws_detail, result.title, subtitle,
        result.boxes, result.circuits, result.components,
        target_brand=target_brand or DEFAULT_TARGET_BRAND,
        sheet_title="箱柜及元器件明细"
    )

    _fill_quotation_summary_sheet(
        ws_summary, result.title,
        result.boxes, result.circuits, result.components,
        card_anchors, detail_sheet_title="箱柜及元器件明细",
        target_brand=target_brand or DEFAULT_TARGET_BRAND,
        sheet_title="配电箱柜汇总表"
    )

    # 导出不被存疑项拦住，就必须把它们如实随文件交出；没有存疑时保持两张表
    if result.uncertainties:
        ws_u = wb.create_sheet("待核对项")
        r_u = _setup(ws_u, f"{result.title}——待核对项", subtitle,
                     ["序号", "位置", "待核对问题", "级别", "状态"], [6, 22, 70, 10, 10])
        items = sorted(result.uncertainties, key=lambda u: bool(u.resolved))
        for i, u in enumerate(items, 1):
            r_u = _row(ws_u, r_u, [i, u.location, u.detail, getattr(u, "severity", "") or "",
                                   "已确认" if u.resolved else "未确认"],
                       height=30, center_cols=(1, 4, 5))


def build_workbook(result: ExtractionResult, subtitle: str, out_path: str,
                   changes=None, include_changes: bool = True,
                   template_path: str = "", target_brand: str = "",
                   layout: str = "all", bidder: str = "", owner: str = "",
                   quote_res: dict | None = None) -> str:
    template = bool(template_path) and os.path.exists(template_path)
    if template:
        try:
            wb = openpyxl.load_workbook(template_path)
        except Exception:  # noqa: BLE001 - 模板坏了不能拖垮导出
            template = False
            wb = _blank_workbook()
    else:
        wb = _blank_workbook()

    if layout in ("2_sheets", "dual_sheets") and not template:
        _fill_dual_sheets(wb, result, subtitle, target_brand=target_brand or DEFAULT_TARGET_BRAND)
    elif layout in ("3_sheets", "quote_template") and not template:
        if quote_res:
            model = quote_model_from_quote_res(quote_res, {"project": result.title}, target_brand or DEFAULT_TARGET_BRAND)
            model["project"] = _clean_proj_title(result.title) or model["project"]
            model["owner"] = owner
            model["unresolved_count"] = len([u for u in (result.uncertainties or [])
                                             if not getattr(u, "resolved", False)])
        else:
            model = quote_model_from_result(result, target_brand or DEFAULT_TARGET_BRAND, owner=owner)
        _fill_quote_template(wb, model)
        _fill_appendix_sheets(wb, result, subtitle)
        if getattr(result, "topology", None):
            _fill_topology_sheet(wb, result.topology, f"{result.title}——配电拓扑架构树", subtitle)
        if include_changes and changes:
            _fill_changes(wb, changes)
        if getattr(result, "reconciliation", None) and result.reconciliation.has_catalog:
            _fill_reconciliation_sheet(wb, result.reconciliation, result.title, subtitle)
    else:
        _fill_sheets(wb, result, subtitle, template, target_brand=target_brand or DEFAULT_TARGET_BRAND)
        if getattr(result, "topology", None):
            _fill_topology_sheet(wb, result.topology, f"{result.title}——配电拓扑架构树", subtitle)
        if getattr(result, "reconciliation", None) and result.reconciliation.has_catalog:
            _fill_reconciliation_sheet(wb, result.reconciliation, result.title, subtitle)
        if result.components:
            _fill_replacements(wb, result.components, target_brand=target_brand or DEFAULT_TARGET_BRAND)
        if include_changes and changes:
            _fill_changes(wb, changes)

    wb.save(out_path)
    return out_path


def build_project_bom_workbook(project_name: str, jobs: list[dict], out_path: str, target_brand: str = "") -> str:
    """生成全项目跨配电箱的大型集中采购总清单（Global BOM）与成套辅料测算表。"""
    from datetime import datetime
    wb = _blank_workbook()

    comp_map: dict = {}
    all_boxes: list = []
    all_reqs: list = []
    seen_reqs: set = set()

    for job in jobs:
        data = job.get("data") or {}
        boxes = data.get("boxes") or []
        components = data.get("components") or []
        requirements = data.get("requirements") or []

        for b in boxes:
            all_boxes.append({
                "code": b.get("code") or job.get("box_code") or "未编号",
                "name": b.get("name") or "配电箱",
                "ip_rating": b.get("ip_rating") or "IP30",
                "install": b.get("install") or "",
                "location": b.get("location") or "",
                "size": b.get("size") or "",
                "quantity": b.get("quantity") or 1,
                "circuits_count": len(data.get("circuits") or []),
            })

        for c in components:
            name = (c.get("name") or "元器件").strip()
            spec = (c.get("spec") or "").strip()
            unit = (c.get("unit") or "只").strip()
            qty = float(c.get("quantity") or 0)
            key = (name, spec, unit)
            if key not in comp_map:
                comp_map[key] = {"name": name, "spec": spec, "unit": unit, "total": 0.0, "boxes": {}, "notes": set()}

            box_label = c.get("used_in") or (boxes[0].get("code") if boxes else job.get("box_code")) or "通用"
            comp_map[key]["total"] += qty
            comp_map[key]["boxes"][box_label] = comp_map[key]["boxes"].get(box_label, 0) + qty
            if c.get("note"):
                comp_map[key]["notes"].add(c.get("note"))

        for r in requirements:
            r_item = r.get("item") or "设计说明"
            r_content = r.get("content") or ""
            r_key = (r_item, r_content)
            if r_key not in seen_reqs:
                seen_reqs.add(r_key)
                all_reqs.append({"item": r_item, "content": r_content})

    # 收集全项目待核对项：导出不再被存疑项拦住，就更要把它们如实随文件交出，
    # 否则"能导出"就变成了"问题被藏起来"。
    all_unresolved: list[dict] = []
    for job in jobs:
        for u in (job.get("data") or {}).get("uncertainties") or []:
            if not isinstance(u, dict):
                continue
            all_unresolved.append({
                "filename": job.get("filename") or job.get("job_id") or "",
                "location": u.get("location") or "",
                "detail": u.get("detail") or "",
                "severity": (u.get("severity") or "WARNING").upper(),
                "resolved": bool(u.get("resolved")),
            })
    pending_count = sum(1 for u in all_unresolved if not u["resolved"])

    subtitle = (f"项目：{project_name} ｜ 涵盖 {len(jobs)} 份图纸、{len(all_boxes)} 台配电箱 ｜ "
                f"生成时间：{datetime.now():%Y-%m-%d %H:%M}"
                + (f" ｜ ⚠ 有 {pending_count} 处待核对项未确认，见「{_SHEETS['unresolved']}」" if pending_count else ""))

    # 1. Sheet 1: 全项目成套设备报价(汇总)
    ws_summary = wb.active
    ws_cards = wb.create_sheet(_SHEETS["box_cards"])

    # 聚合所有回路与元器件
    all_circuits = []
    all_components = []
    for job in jobs:
        d = job.get("data") or {}
        all_circuits.extend(d.get("circuits") or [])
        all_components.extend(d.get("components") or [])

    # 渲染垂直流水卡片分项明细，获得各箱锚点行号
    card_anchors = _fill_box_cards_detail_sheet(
        ws_cards, project_name, subtitle,
        all_boxes, all_circuits, all_components,
        target_brand=target_brand or DEFAULT_TARGET_BRAND
    )

    # 渲染全项目成套报价汇总表（带超链接锚定卡片）
    _fill_quotation_summary_sheet(
        ws_summary, project_name,
        all_boxes, all_circuits, all_components,
        card_anchors, detail_sheet_title=_SHEETS["box_cards"],
        target_brand=target_brand or DEFAULT_TARGET_BRAND
    )

    # 2. Sheet 3: 全项目采购总清单 (BOM)
    ws1 = wb.create_sheet(_SHEETS["bom"])
    headers1 = ["序号", "元器件名称", "规格型号", "单位", "全项目总采购量", "各配电箱分布明细", "参考单价(元)", "预估合价(元)", "备注"]
    r1 = _setup(ws1, f"【{project_name}】电气元器件集中采购总清单(BOM)", subtitle, headers1, _COLUMNS["bom"])

    def _sort_key(item):
        name = item["name"]
        spec = item["spec"]
        if "塑壳" in name or "MCCB" in spec or "隔离开关" in name:
            rank = 1
        elif "断路器" in name or "微断" in name or "漏电" in name or "MCB" in spec or "RCB" in spec:
            rank = 2
        elif "接触器" in name or "继电器" in name:
            rank = 3
        elif "浪涌" in name or "电表" in name or "互感器" in name or "SPD" in spec:
            rank = 4
        else:
            rank = 5
        return (rank, name, spec)

    sorted_comps = sorted(comp_map.values(), key=_sort_key)
    for i, c in enumerate(sorted_comps, 1):
        q = int(c["total"]) if float(c["total"]) == int(c["total"]) else round(c["total"], 2)
        dist_str = "；".join(f"{b} ({int(k) if float(k) == int(k) else k}{c['unit']})" for b, k in c["boxes"].items())
        notes_str = "；".join(c["notes"])
        curr_row = r1
        r1 = _row(ws1, r1, [i, c["name"], c["spec"], c["unit"], q, dist_str, "", f"=E{curr_row}*G{curr_row}", notes_str], height=32, center_cols=(1, 4))

    # Sheet 2: 集中采购国产化平替对账表 (针对 target_brand)
    from .catalog import analyze_components_replacement
    raw_for_rep = [
        {"name": c["name"], "spec": c["spec"], "quantity": c["total"], "unit": c["unit"], "used_in": "；".join(c["boxes"].keys())}
        for c in sorted_comps
    ]
    rep_analysis = analyze_components_replacement(raw_for_rep, target_brand=target_brand)
    ws_rep = wb.create_sheet(f"{_SHEETS['replacements_prefix']}({target_brand})")
    headers_rep = ["序号", "元器件名称", "原图设计规格", "原厂品牌", f"推荐平替型号({target_brand})", "集中采购总量", "单位", "预计降本", "各配电箱分布", "对标依据与核验说明"]
    rep_sub = (
        f"全项目集中平替目标品牌：{target_brand} ｜ 涉及品种：{len(sorted_comps)} 项 ｜ "
        f"可降本采购总量：{rep_analysis['replaceable_quantity']} 件 ｜ 预计元器件总采购额直降约：{rep_analysis['estimated_overall_saving_pct']}%"
    )
    r_rep = _setup(ws_rep, f"【{project_name}】电气元器件集中采购国产化平替与降本对账表（{target_brand}）", rep_sub, headers_rep,
                   [6, 20, 26, 12, 32, 14, 8, 12, 36, 42])
    save_font = Font(name=_FONT_FAMILY, size=10, bold=True, color=_PALETTE["saving_text"])
    warn_font = Font(name=_FONT_FAMILY, size=10, bold=True, color=_PALETTE["warning_text"])
    for i, it in enumerate(rep_analysis["items"], 1):
        sav_pct = it.get("estimated_saving_pct")
        sav_status = it.get("saving_status")
        if sav_pct is not None and sav_pct > 0:
            saving_text = f"↓{sav_pct}%"
        elif sav_status == "cost_increase":
            saving_text = "成本增加"
        elif sav_status == "cost_equal":
            saving_text = "平价"
        elif sav_pct is None or sav_status == "missing_price":
            saving_text = "待询价"
        else:
            saving_text = "已最优"

        curr_r = r_rep
        r_rep = _row(ws_rep, r_rep, [
            i, it["name"], it["original_spec"], it["original_brand"],
            it["recommended_model"], it["quantity"], it["unit"],
            saving_text, it["used_in"], it["notes"]
        ], height=32, center_cols=(1, 4, 7, 8))
        if sav_pct is not None and sav_pct > 0:
            ws_rep.cell(row=curr_r, column=8).font = save_font
        elif sav_status == "cost_increase":
            ws_rep.cell(row=curr_r, column=8).font = warn_font

    # Sheet 3: 配电箱成套设备台账
    ws2 = wb.create_sheet(_SHEETS["ledger"])
    headers2 = ["序号", "配电箱编号", "设备名称", "防护等级", "安装方式", "安装位置", "参考尺寸(mm)", "台数", "回路总数", "备注"]
    r2 = _setup(ws2, f"【{project_name}】配电箱/配电柜成套台账", subtitle, headers2, _COLUMNS["ledger"])
    for i, b in enumerate(all_boxes, 1):
        r2 = _row(ws2, r2, [i, b["code"], b["name"], b["ip_rating"], b["install"], b["location"], b["size"], b["quantity"], b["circuits_count"], ""], height=26, center_cols=(1, 4, 8, 9))

    # Sheet 3: 成套外壳与辅材概算
    ws3 = wb.create_sheet(_SHEETS["aux_estimate"])
    headers3 = ["序号", "配电箱编号", "回路数", "外壳估算形式/尺寸", "箱壳估算基准(元)", "铜排母线及端子辅料(元)", "装配测试工时费(元)", "单台成套制造辅价(元)", "台数", "小计(元)"]
    r3 = _setup(ws3, f"【{project_name}】配电箱成套辅料与柜体制造费测算", "基于工程经验的辅材、箱体钣金与组装试验费估算模板（单价公式可按项目调整）", headers3, _COLUMNS["aux_estimate"])
    for i, b in enumerate(all_boxes, 1):
        c_cnt = max(1, b["circuits_count"])
        box_base = (_ESTIMATION["enclosure_base_surface"] if "明装" in b["install"]
                    else _ESTIMATION["enclosure_base_floor"])
        acc_base = c_cnt * _ESTIMATION["accessory_per_circuit"]
        labor_base = c_cnt * _ESTIMATION["labor_per_circuit"] + _ESTIMATION["labor_fixed"]
        curr_row = r3
        r3 = _row(ws3, r3, [i, b["code"], c_cnt, b["size"] or f"{c_cnt}极外壳", box_base, acc_base, labor_base, f"=E{curr_row}+F{curr_row}+G{curr_row}", b["quantity"], f"=H{curr_row}*I{curr_row}"], height=26, center_cols=(1, 3, 9))

    # Sheet 4: 项目统一技术规范与说明
    ws4 = wb.create_sheet(_SHEETS["project_requirements"])
    headers4 = ["序号", "项目/分类", "要求内容及设计原则"]
    r4 = _setup(ws4, f"【{project_name}】全项目电气技术要求汇总", "汇集该项目全部图纸的设计说明、分断能力标准及特殊保护原则", headers4, _COLUMNS["project_requirements"])
    for i, req in enumerate(all_reqs, 1):
        r4 = _row(ws4, r4, [i, req["item"], req["content"]], height=32, center_cols=(1,))

    # Sheet 5: 全项目配电系统拓扑树与电气设备分级
    from .assemble import build_distribution_topology
    from .schema import Box, Circuit
    proj_boxes = []
    proj_circuits = []
    for job in jobs:
        d = job.get("data") or {}
        for b in d.get("boxes") or []:
            try:
                proj_boxes.append(Box.model_validate(b))
            except Exception:
                pass
        for c in d.get("circuits") or []:
            try:
                proj_circuits.append(Circuit.model_validate(c))
            except Exception:
                pass

    proj_topology = build_distribution_topology(proj_boxes, proj_circuits)
    if proj_topology:
        _fill_topology_sheet(
            wb, proj_topology,
            f"【{project_name}】全项目配电系统拓扑树与电气设备分级",
            f"跨图纸层级关联：涵盖全项目 {len(all_boxes)} 台配电柜/箱及一二次控制原理图"
        )

    # 最后一页：待核对项总表（有则列出，没有也给出说明，避免读者误以为漏了）
    ws_u = wb.create_sheet(_SHEETS["unresolved"])
    headers_u = ["序号", "来源图纸", "位置", "待核对问题", "级别", "状态"]
    r_u = _setup(
        ws_u, f"【{project_name}】全项目待核对项",
        (f"跨 {len(jobs)} 份图纸汇总 {len(all_unresolved)} 条，其中未确认 {pending_count} 条"
         "；导出不被这些项遮挡，但报价前应逐条核对"),
        headers_u, _COLUMNS["unresolved"])
    if all_unresolved:
        # 未确认的排前面，同级别内按图纸名稳定排序
        all_unresolved.sort(key=lambda u: (u["resolved"], u["filename"], u["severity"]))
        for i, u in enumerate(all_unresolved, 1):
            r_u = _row(ws_u, r_u,
                       [i, u["filename"], u["location"], u["detail"], u["severity"],
                        "已确认" if u["resolved"] else "未确认"],
                       height=30, center_cols=(1, 5, 6))
    else:
        _row(ws_u, r_u, ["", "", "", "本项目的图纸没有待核对项", "", ""], height=30, center_cols=(1,))

    wb.save(out_path)
    return out_path


def build_custom_table_workbook(title: str, headers: list[str], rows: list[list[Any]], subtitle: str = "") -> openpyxl.Workbook:
    """构建自定义/AI智能对话动态导出的 Excel 工作簿。
    
    支持根据用户提问内容自由生成工整专业的高颜值表格，支持数字自动对齐与列宽自适应。
    """
    from datetime import datetime

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = (title[:28] if title else "数据统计表").replace("/", "_").replace("\\", "_")

    col_cnt = max(len(headers), 1)

    # 1. 标题行
    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=col_cnt)
    c1 = ws.cell(row=1, column=1, value=title or "数据整理统计表")
    c1.font = TITLE_FONT
    c1.alignment = CENTER
    ws.row_dimensions[1].height = 32

    # 2. 副标题 / 说明
    sub_text = subtitle or f"生成时间：{datetime.now():%Y-%m-%d %H:%M} ｜ 由 AI 配电箱成套专家智能整理生成"
    ws.merge_cells(start_row=2, start_column=1, end_row=2, end_column=col_cnt)
    c2 = ws.cell(row=2, column=1, value=sub_text)
    c2.font = SUB_FONT
    c2.alignment = CENTER
    ws.row_dimensions[2].height = 20

    # 3. 表头
    for j, h in enumerate(headers, start=1):
        cell = ws.cell(row=3, column=j, value=h)
        cell.font = HDR_FONT
        cell.fill = HDR_FILL
        cell.alignment = CENTER
        cell.border = BORDER
    ws.row_dimensions[3].height = 26

    # 4. 数据行
    col_max_lens = [len(str(h).encode("gbk", "ignore")) for h in headers]
    for r_idx, row_vals in enumerate(rows, start=4):
        ws.row_dimensions[r_idx].height = 24
        for c_idx, val in enumerate(row_vals, start=1):
            cell = ws.cell(row=r_idx, column=c_idx, value=sanitize_excel_value(val))
            cell.font = CELL_FONT
            cell.border = BORDER

            # 文本与数字对齐
            is_number = isinstance(val, (int, float))
            if not is_number and isinstance(val, str):
                cleaned_val = val.replace(",", "").strip()
                if cleaned_val.replace(".", "", 1).isdigit() and len(cleaned_val) < 12:
                    is_number = True

            if c_idx == 1 and not is_number:
                cell.alignment = CENTER
            elif is_number:
                cell.alignment = RIGHT
                if isinstance(val, float):
                    cell.number_format = "#,##0.00"
            else:
                cell.alignment = LEFT

            val_len = len(str(val or "").encode("gbk", "ignore"))
            if c_idx - 1 < len(col_max_lens):
                col_max_lens[c_idx - 1] = max(col_max_lens[c_idx - 1], val_len)

    # 5. 设置列宽
    for j, max_len in enumerate(col_max_lens, start=1):
        width = max(10, min(max_len + 4, 45))
        ws.column_dimensions[get_column_letter(j)].width = width

    return wb


def build_pricing_quotation_workbook(
    job_info: dict,
    quote_res: dict,
    out_path: str,
    target_brand: str = ""
) -> str:
    """组价阶段导出：屏柜汇总表 + 屏柜分项表（客户口径的两表模板）。

    数据来自组价工作台的核算结果（含用户手改价、平替品牌、费率联动），
    版面由 quote_template 渲染器统一负责，不再各写一套。
    """
    info = job_info or {}
    model = quote_model_from_quote_res(quote_res, info, target_brand or DEFAULT_TARGET_BRAND)
    model["project"] = model["project"] or _clean_proj_title(info.get("title") or "")
    model["owner"] = info.get("owner") or ""
    data = info.get("data") or {}
    items = data.get("uncertainties") or (info.get("summary") or {}).get("uncertainties") or []
    model["unresolved_count"] = len([u for u in items if isinstance(u, dict) and not u.get("resolved")])

    wb = _blank_workbook()
    _fill_quote_template(wb, model)
    wb.save(out_path)
    return out_path


def build_takeoff_bom_workbook(
    job_info: dict,
    out_path: str
) -> str:
    """生成工程量拆量清单（双 Sheet 纯工程量标准，去除所有价格）：
    Sheet 1: 配电箱柜清单汇总 —— 汇总每个箱体的型号、防护等级、安装方式、数量、回路数、安装位置等纯工程量信息，序号带超链接直达明细卡片；
    Sheet 2: 箱柜及元器件明细 —— 保持与组价明细完全一致的包含关系，展开每个箱体及箱内回路/元器件明细，不含单价、合价与纯价格费率行。
    """
    from datetime import datetime

    data = job_info.get("data") or {}
    boxes = data.get("boxes") or []
    circuits = data.get("circuits") or []
    components = data.get("components") or []

    project_title = job_info.get("project") or (job_info.get("summary") or {}).get("title") or "配电箱工程量拆量"
    clean_proj_name = str(project_title).replace("——成套箱体分项卡片明细表", "").replace("图纸扒图_", "").strip()
    filename = job_info.get("filename") or f"{job_info.get('job_id', 'export')}.dwg"

    wb = openpyxl.Workbook()
    ws_summary = wb.active
    ws_summary.title = "配电箱柜清单汇总"
    ws_detail = wb.create_sheet("箱柜及元器件明细")

    # 将回路与元器件按箱体归类
    circuits_by_box: dict[str, list] = {}
    for c in circuits:
        b_code = c.get("box") or c.get("box_code") or ""
        circuits_by_box.setdefault(b_code, []).append(c)

    comps_by_box: dict[str, list] = {}
    for comp in components:
        used = str(comp.get("used_in") or comp.get("box") or comp.get("box_code") or "").strip()
        parts = [p.strip() for p in re.split(r"[、,，\s]+", used) if p.strip()]
        if not parts and used:
            parts = [used.strip()]
        for p in parts:
            comps_by_box.setdefault(p, []).append(comp)

    # ==========================================================
    # Sheet 2: 箱柜及元器件明细（纯工程量，去掉价格）
    # ==========================================================
    detail_widths = [8, 14, 24, 28, 8, 10, 16, 24, 28, 20]
    for j, w in enumerate(detail_widths, start=1):
        ws_detail.column_dimensions[get_column_letter(j)].width = w

    ws_detail.merge_cells("A1:J1")
    t1 = ws_detail.cell(row=1, column=1, value="配电箱柜及元器件拆量明细表 (纯工程量)")
    t1.font = TITLE_FONT
    t1.alignment = CENTER
    ws_detail.row_dimensions[1].height = 32

    sub_text = (f"工程项目：{clean_proj_name} ｜ 图纸依据：{filename} ｜ "
                f"工程量属性：施工图提取未组价纯工程量 ｜ 导出时间：{datetime.now():%Y-%m-%d %H:%M}")
    ws_detail.merge_cells("A2:J2")
    t2 = ws_detail.cell(row=2, column=1, value=sub_text)
    t2.font = SUB_FONT
    t2.alignment = CENTER
    ws_detail.row_dimensions[2].height = 20

    card_anchors: dict[str, int] = {}
    curr_d = 4
    headers_detail = ["序号", "柜号", "元件名称", "图纸设计规格", "单位", "数量", "所属回路/部位", "负荷用途/负荷名称", "导线规格及敷设", "工程备注"]

    total_items_count = 0

    for b_idx, box in enumerate(boxes, start=1):
        box_code = box.get("code") or box.get("box_code") or f"BOX{b_idx}"
        box_name = box.get("name") or "配电箱"
        box_size = box.get("size") or "-"
        box_ip = box.get("ip_rating") or "IP30"
        box_install = box.get("install") or "明装"
        box_loc = box.get("location") or "待确认"
        box_qty = int(box.get("quantity") or 1)

        b_circuits = circuits_by_box.get(box_code, [])
        b_comps = comps_by_box.get(box_code, [])

        card_anchors[box_code] = curr_d

        # 1. 箱柜主行 (水蓝横幅)
        for col in range(1, 11):
            cell = ws_detail.cell(row=curr_d, column=col)
            cell.fill = BOX_HEADER_FILL
            cell.border = DARK_BORDER

        ws_detail.cell(row=curr_d, column=1, value=f"1-{b_idx}").font = BOX_HEADER_FONT
        ws_detail.cell(row=curr_d, column=1).alignment = CENTER

        ws_detail.cell(row=curr_d, column=2, value=box_code).font = BOX_HEADER_FONT
        ws_detail.cell(row=curr_d, column=2).alignment = LEFT

        ws_detail.cell(row=curr_d, column=3, value=f"{box_name} (共{len(b_circuits)}回路 / {len(b_comps)}项元器件)").font = BOX_HEADER_FONT
        ws_detail.cell(row=curr_d, column=3).alignment = LEFT

        ws_detail.cell(row=curr_d, column=4, value=box_size).font = BOX_HEADER_FONT
        ws_detail.cell(row=curr_d, column=4).alignment = LEFT

        ws_detail.cell(row=curr_d, column=5, value="台").font = BOX_HEADER_FONT
        ws_detail.cell(row=curr_d, column=5).alignment = CENTER

        ws_detail.cell(row=curr_d, column=6, value=box_qty).font = BOX_HEADER_FONT
        ws_detail.cell(row=curr_d, column=6).alignment = CENTER

        ws_detail.cell(row=curr_d, column=7, value=box_loc).font = BOX_HEADER_FONT
        ws_detail.cell(row=curr_d, column=7).alignment = LEFT

        ws_detail.cell(row=curr_d, column=8, value=f"{box_install} ｜ {box_ip}").font = BOX_HEADER_FONT
        ws_detail.cell(row=curr_d, column=8).alignment = LEFT

        ws_detail.cell(row=curr_d, column=9, value="-").font = BOX_HEADER_FONT
        ws_detail.cell(row=curr_d, column=9).alignment = CENTER

        ws_detail.cell(row=curr_d, column=10, value="箱体基本参数").font = BOX_HEADER_FONT
        ws_detail.cell(row=curr_d, column=10).alignment = LEFT

        ws_detail.row_dimensions[curr_d].height = 26
        curr_d += 1

        # 2. 表头行
        for j, h in enumerate(headers_detail, start=1):
            hc = ws_detail.cell(row=curr_d, column=j, value=h)
            hc.font = SUMMARY_HDR_FONT
            hc.fill = SUMMARY_HDR_FILL
            hc.alignment = CENTER
            hc.border = DARK_BORDER
        ws_detail.row_dimensions[curr_d].height = 24
        curr_d += 1

        # 3. 组织该箱体的明细行：回路器件 + 独立器件
        c_seq_idx = 1

        # A. 回路列表（进线与分支断路器）
        for cir in b_circuits:
            c_no = cir.get("circuit_no") or ""
            load = cir.get("load_name") or ""
            brk = cir.get("breaker") or ""
            cable = cir.get("cable") or ""
            contactor = cir.get("contactor") or ""
            power = cir.get("power_kw") or ""
            phase = cir.get("phase") or ""
            note = cir.get("note") or ""

            is_inc = "进线" in c_no or "进线" in load
            dev_name = "进线断路器" if is_inc else "分支断路器"
            if contactor and contactor != "-":
                dev_name += f" / 接触器({contactor})"

            extra_info = f"相序:{phase} 容量:{power}kW" if (power or phase) else note

            row_vals = [
                f"{b_idx}.{c_seq_idx}",
                box_code,
                dev_name,
                brk or "(规格未标注)",
                "只",
                1,
                c_no or "回路",
                load or "负荷出线",
                cable or "-",
                extra_info
            ]
            for j, val in enumerate(row_vals, start=1):
                cell = ws_detail.cell(row=curr_d, column=j, value=sanitize_excel_value(val))
                cell.font = CELL_FONT
                cell.border = DARK_BORDER
                cell.alignment = CENTER if j in (1, 5, 6) else LEFT
            ws_detail.row_dimensions[curr_d].height = 22
            curr_d += 1
            c_seq_idx += 1
            total_items_count += 1

        # B. 附属/独立元器件（如 SPD、电表、互感器、接触器等）
        for cp in b_comps:
            cp_name = cp.get("name") or "元器件"
            if cp_name == "配电箱体":
                continue
            cp_spec = cp.get("spec") or ""
            cp_qty = int(cp.get("quantity") or 1)
            cp_unit = cp.get("unit") or "只"
            cp_note = cp.get("note") or ""

            row_vals = [
                f"{b_idx}.{c_seq_idx}",
                box_code,
                cp_name,
                cp_spec or "-",
                cp_unit,
                cp_qty,
                "箱内装置",
                "保护与测量附件",
                "-",
                cp_note
            ]
            for j, val in enumerate(row_vals, start=1):
                cell = ws_detail.cell(row=curr_d, column=j, value=sanitize_excel_value(val))
                cell.font = CELL_FONT
                cell.border = DARK_BORDER
                cell.alignment = CENTER if j in (1, 5, 6) else LEFT
            ws_detail.row_dimensions[curr_d].height = 22
            curr_d += 1
            c_seq_idx += 1
            total_items_count += cp_qty

        # 间隔两行空白
        curr_d += 2

    # 底部全项目工程量总计行
    ws_detail.merge_cells(start_row=curr_d, start_column=1, end_row=curr_d, end_column=5)
    t_takeoff_lbl = ws_detail.cell(row=curr_d, column=1, value="全项目工程量总计 (全部箱柜设备与元器件总件数)")
    t_takeoff_lbl.font = TOTAL_FONT
    t_takeoff_lbl.alignment = CENTER
    t_takeoff_lbl.fill = TOTAL_FILL
    for col in range(1, 6):
        ws_detail.cell(row=curr_d, column=col).border = DOUBLE_BOTTOM_BORDER
        ws_detail.cell(row=curr_d, column=col).fill = TOTAL_FILL
    ws_detail.cell(row=curr_d, column=6, value=total_items_count).alignment = CENTER
    ws_detail.cell(row=curr_d, column=6).font = TOTAL_FONT
    ws_detail.cell(row=curr_d, column=6).fill = TOTAL_FILL
    ws_detail.cell(row=curr_d, column=6).border = DOUBLE_BOTTOM_BORDER
    for col in range(7, 11):
        ws_detail.cell(row=curr_d, column=col).fill = TOTAL_FILL
        ws_detail.cell(row=curr_d, column=col).border = DOUBLE_BOTTOM_BORDER
    ws_detail.row_dimensions[curr_d].height = 28

    # ==========================================================
    # Sheet 1: 配电箱柜清单汇总（纯工程量）
    # ==========================================================
    sum_widths = [10, 16, 26, 20, 14, 14, 8, 10, 12, 22, 24]
    for j, w in enumerate(sum_widths, start=1):
        ws_summary.column_dimensions[get_column_letter(j)].width = w

    ws_summary.merge_cells("A1:K1")
    s_title = ws_summary.cell(row=1, column=1, value="配电箱柜清单汇总表 (工程量拆量)")
    s_title.font = SUMMARY_TITLE_FONT
    s_title.alignment = CENTER
    ws_summary.row_dimensions[1].height = 36

    ws_summary.row_dimensions[2].height = 10

    ws_summary.cell(row=3, column=1, value="项目单位：").font = META_FONT
    ws_summary.row_dimensions[3].height = 20

    ws_summary.cell(row=4, column=1, value=f"项目名称：{clean_proj_name}").font = META_FONT
    ws_summary.row_dimensions[4].height = 20

    ws_summary.cell(row=5, column=1, value=f"图纸文件：{filename} ｜ 拆量核对完成 ｜ 纯工程量底稿").font = META_FONT
    ws_summary.row_dimensions[5].height = 20

    ws_summary.merge_cells("A6:K6")
    cat_c = ws_summary.cell(row=6, column=1, value="配电箱")
    cat_c.font = CATEGORY_FONT
    cat_c.fill = CATEGORY_FILL
    cat_c.alignment = Alignment(horizontal="left", vertical="center", indent=1)
    for col in range(1, 12):
        ws_summary.cell(row=6, column=col).border = DARK_BORDER
    ws_summary.row_dimensions[6].height = 24

    headers_summary = ["序号", "柜号", "箱柜名称", "型号规格", "防护等级", "安装方式", "单位", "数量", "回路总数", "安装位置/使用部位", "工程备注"]
    for j, h in enumerate(headers_summary, start=1):
        hc = ws_summary.cell(row=7, column=j, value=h)
        hc.font = SUMMARY_HDR_FONT
        hc.fill = SUMMARY_HDR_FILL
        hc.alignment = CENTER
        hc.border = DARK_BORDER
    ws_summary.row_dimensions[7].height = 28

    start_s_r = 8
    curr_s_r = start_s_r

    for i, b in enumerate(boxes, start=1):
        box_code = b.get("code") or b.get("box_code") or f"BOX{i}"
        box_name = b.get("name") or "配电箱"
        box_size = b.get("size") or "-"
        box_ip = b.get("ip_rating") or "IP30"
        box_install = b.get("install") or "明装"
        box_qty = int(b.get("quantity") or 1)
        b_circuits = circuits_by_box.get(box_code, [])
        box_loc = b.get("location") or "待确认"
        box_note = b.get("note") or ""

        c_seq = ws_summary.cell(row=curr_s_r, column=1, value=i)
        c_seq.alignment = CENTER
        c_seq.border = DARK_BORDER
        target_r = card_anchors.get(box_code)
        if target_r:
            c_seq.hyperlink = f"#'箱柜及元器件明细'!A{target_r}"
            c_seq.font = LINK_FONT
        else:
            c_seq.font = CELL_FONT

        c_code = ws_summary.cell(row=curr_s_r, column=2, value=box_code)
        c_code.font = CELL_FONT
        c_code.alignment = LEFT
        c_code.border = DARK_BORDER

        c_name = ws_summary.cell(row=curr_s_r, column=3, value=box_name)
        c_name.font = CELL_FONT
        c_name.alignment = LEFT
        c_name.border = DARK_BORDER

        c_size = ws_summary.cell(row=curr_s_r, column=4, value=box_size)
        c_size.font = CELL_FONT
        c_size.alignment = LEFT
        c_size.border = DARK_BORDER

        c_ip = ws_summary.cell(row=curr_s_r, column=5, value=box_ip)
        c_ip.font = CELL_FONT
        c_ip.alignment = CENTER
        c_ip.border = DARK_BORDER

        c_ins = ws_summary.cell(row=curr_s_r, column=6, value=box_install)
        c_ins.font = CELL_FONT
        c_ins.alignment = CENTER
        c_ins.border = DARK_BORDER

        c_u = ws_summary.cell(row=curr_s_r, column=7, value="台")
        c_u.font = CELL_FONT
        c_u.alignment = CENTER
        c_u.border = DARK_BORDER

        c_q = ws_summary.cell(row=curr_s_r, column=8, value=box_qty)
        c_q.font = CELL_FONT
        c_q.alignment = CENTER
        c_q.border = DARK_BORDER

        c_cir = ws_summary.cell(row=curr_s_r, column=9, value=len(b_circuits))
        c_cir.font = CELL_FONT
        c_cir.alignment = CENTER
        c_cir.border = DARK_BORDER

        c_loc = ws_summary.cell(row=curr_s_r, column=10, value=box_loc)
        c_loc.font = CELL_FONT
        c_loc.alignment = LEFT
        c_loc.border = DARK_BORDER

        c_rem = ws_summary.cell(row=curr_s_r, column=11, value=box_note)
        c_rem.font = CELL_FONT
        c_rem.alignment = LEFT
        c_rem.border = DARK_BORDER

        ws_summary.row_dimensions[curr_s_r].height = 24
        curr_s_r += 1

    last_s_r = curr_s_r - 1
    ws_summary.merge_cells(start_row=curr_s_r, start_column=1, end_row=curr_s_r, end_column=7)
    t_lbl = ws_summary.cell(row=curr_s_r, column=1, value="合    计")
    t_lbl.font = TOTAL_FONT
    t_lbl.alignment = CENTER
    t_lbl.fill = TOTAL_FILL
    for col in range(1, 8):
        ws_summary.cell(row=curr_s_r, column=col).border = DOUBLE_BOTTOM_BORDER
        ws_summary.cell(row=curr_s_r, column=col).fill = TOTAL_FILL

    c_sqty = ws_summary.cell(row=curr_s_r, column=8, value=f"=SUM(H{start_s_r}:H{last_s_r})")
    c_sqty.font = TOTAL_FONT
    c_sqty.alignment = CENTER
    c_sqty.fill = TOTAL_FILL
    c_sqty.border = DOUBLE_BOTTOM_BORDER

    c_scir = ws_summary.cell(row=curr_s_r, column=9, value=f"=SUM(I{start_s_r}:I{last_s_r})")
    c_scir.font = TOTAL_FONT
    c_scir.alignment = CENTER
    c_scir.fill = TOTAL_FILL
    c_scir.border = DOUBLE_BOTTOM_BORDER

    for col in (10, 11):
        ws_summary.cell(row=curr_s_r, column=col).fill = TOTAL_FILL
        ws_summary.cell(row=curr_s_r, column=col).border = DOUBLE_BOTTOM_BORDER
    ws_summary.row_dimensions[curr_s_r].height = 28

    # 未确认存疑如实随文件交出，防止关键设计存疑漏项
    raw_u = (job_info.get("data") or {}).get("uncertainties") or (job_info.get("summary") or {}).get("uncertainties") or []
    if raw_u:
        ws_u = wb.create_sheet("待核对项")
        sub_u = f"工程项目：{clean_proj_name} ｜ 未确认的图纸存疑与设计缺失项（随文件如实交付，报价前应逐条核对）"
        r_u = _setup(ws_u, f"{clean_proj_name}——待核对项", sub_u,
                     ["序号", "位置", "待核对问题", "级别", "状态"], [6, 22, 70, 10, 10])

        def _is_res(item):
            if isinstance(item, dict):
                return bool(item.get("resolved"))
            return bool(getattr(item, "resolved", False))

        sorted_u = sorted(raw_u, key=_is_res)
        for idx, u in enumerate(sorted_u, 1):
            if isinstance(u, dict):
                loc = u.get("location") or ""
                det = u.get("detail") or u.get("text") or ""
                sev = (u.get("severity") or "WARNING").upper()
                res = bool(u.get("resolved"))
            else:
                loc = getattr(u, "location", "") or ""
                det = getattr(u, "detail", "") or getattr(u, "text", "") or ""
                sev = (getattr(u, "severity", "WARNING") or "WARNING").upper()
                res = bool(getattr(u, "resolved", False))
            r_u = _row(ws_u, r_u, [idx, loc, det, sev, "已确认" if res else "未确认"],
                       height=30, center_cols=(1, 4, 5))

    wb.save(out_path)
    return out_path
