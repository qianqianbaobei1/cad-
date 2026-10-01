# -*- coding: utf-8 -*-
"""Build the quotation workbook from an ExtractionResult."""
import openpyxl
import os
from typing import Any
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter

from .schema import ExtractionResult


def uncertainty_texts(result: ExtractionResult, source: str | None = None) -> list[str]:
    """导出与旧接口沿用的单行写法，已确认项加前缀。source 过滤模型事实或程序告警。"""
    return [f"（已确认）{item.text}" if item.resolved else item.text
            for item in result.uncertainties
            if item.text and (source is None or item.source == source)]

HDR_FONT = Font(name="微软雅黑", size=11, bold=True, color="FFFFFF")
HDR_FILL = PatternFill("solid", fgColor="4472C4")
TITLE_FONT = Font(name="微软雅黑", size=14, bold=True)
SUB_FONT = Font(name="微软雅黑", size=10, color="404040")
META_FONT = Font(name="微软雅黑", size=10, color="000000")
CELL_FONT = Font(name="微软雅黑", size=10)
thin = Side(style="thin", color="BFBFBF")
dark_thin = Side(style="thin", color="595959")
BORDER = Border(left=thin, right=thin, top=thin, bottom=thin)
DARK_BORDER = Border(left=dark_thin, right=dark_thin, top=dark_thin, bottom=dark_thin)
DOUBLE_BOTTOM_BORDER = Border(left=dark_thin, right=dark_thin, top=dark_thin, bottom=Side(style="double", color="000000"))
CENTER = Alignment(horizontal="center", vertical="center", wrap_text=True)
LEFT = Alignment(horizontal="left", vertical="center", wrap_text=True)
RIGHT = Alignment(horizontal="right", vertical="center", wrap_text=True)

# 行业级成套设备报价规范配色（对标行业实际成套厂出图标准）
SUMMARY_TITLE_FONT = Font(name="微软雅黑", size=16, bold=True)
SUMMARY_HDR_FILL = PatternFill("solid", fgColor="D9D9D9")  # 截图同款中浅灰表头
SUMMARY_HDR_FONT = Font(name="微软雅黑", size=10, bold=True, color="000000")
CATEGORY_FILL = PatternFill("solid", fgColor="E2EFDA")  # 截图同款配电箱绿色条目
CATEGORY_FONT = Font(name="微软雅黑", size=11, bold=True, color="274E13")
CARD_HDR_FILL = PatternFill("solid", fgColor="2F5597")  # 垂直流水卡片深蓝标题栏
CARD_SUB_FILL = PatternFill("solid", fgColor="DDEBF7")  # 成本小计淡浅蓝条
TOTAL_FILL = PatternFill("solid", fgColor="F2F2F2")
TOTAL_FONT = Font(name="微软雅黑", size=10, bold=True)
LINK_FONT = Font(name="微软雅黑", size=10, color="0563C1", underline="single", bold=True)
WHITE_BOLD_FONT = Font(name="微软雅黑", size=10, bold=True, color="FFFFFF")
DARK_BOLD_FONT = Font(name="微软雅黑", size=10, bold=True, color="1F497D")


def _val(obj: Any, key: str, default: Any = "") -> Any:
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _estimate_box_costs(box: Any, box_circuits: list[Any], box_components: list[Any]) -> dict[str, float]:
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
        })
    comp_dicts = []
    for comp in box_components:
        comp_dicts.append({
            "name": str(_val(comp, "name", "")),
            "spec": str(_val(comp, "spec", "")),
            "quantity": _val(comp, "quantity", 1),
        })

    # 领域智能推断：双电源箱柜(AT* / ALE*)若进线漏标ATS，依据容量自动补入双电源切换开关
    code_upper = b_dict["box_code"].upper()
    name_upper = b_dict["box_name"].upper()
    is_dual = code_upper.startswith("AT") or code_upper.startswith("ALE") or "双电源" in name_upper or "应急" in name_upper
    has_ats = any("ATS" in c.get("breaker_spec", "").upper() for c in circ_dicts) or any("ATS" in comp.get("spec", "").upper() for comp in comp_dicts)
    if is_dual and not has_ats:
        main_a = 160 if ("160" in code_upper or len(circ_dicts) > 15) else (100 if len(circ_dicts) > 6 else 63)
        comp_dicts.append({
            "name": "双电源自动转换开关",
            "spec": f"ATS-4P-{main_a}A",
            "quantity": 1,
        })

    q = calculate_box_quotation(b_dict, circ_dicts, comp_dicts, brand="正泰")
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
        c = ws.cell(row=r, column=j, value=v)
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


def _fill_box_cards_detail_sheet(ws, title: str, subtitle: str, boxes: list[Any], circuits: list[Any], components: list[Any]) -> dict[str, int]:
    """生成按箱体垂直流水卡片展开的分项明细表，返回每个箱体卡片起始行号以供汇总表做超链接跳转。"""
    ws.title = "箱体分项明细"
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
    widths = [6, 14, 24, 16, 26, 14, 12, 10, 32, 24]
    for j, w in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(j)].width = w

    # 将回路与器件按箱体归类
    circuits_by_box: dict[str, list[Any]] = {}
    for c in circuits:
        b_code = getattr(c, "box", "") if hasattr(c, "box") else str(c.get("box", ""))
        circuits_by_box.setdefault(b_code, []).append(c)

    comps_by_box: dict[str, list[Any]] = {}
    for comp in components:
        used = getattr(comp, "used_in", "") if hasattr(comp, "used_in") else str(comp.get("used_in", ""))
        comps_by_box.setdefault(used, []).append(comp)

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
        costs = _estimate_box_costs(b, b_circuits, b_comps)

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
                    cell = ws.cell(row=r, column=j, value=v)
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
            cp_cell.font = Font(name="微软雅黑", size=9, color="333333")
            cp_cell.alignment = Alignment(horizontal="left", vertical="center")
            cp_cell.fill = PatternFill("solid", fgColor="F7F7F7")
            cp_cell.border = BORDER
            ws.row_dimensions[r].height = 22
            r += 1

        # 每个箱体卡片留 2 行空白隔离
        r += 2

    return card_anchors


def _fill_quotation_summary_sheet(ws, project_title: str, boxes: list[Any], circuits: list[Any], components: list[Any], card_anchors: dict[str, int], detail_sheet_title: str = "箱体分项明细"):
    """高标准复刻成套设备报价(汇总)报表：
    表头包含项目单位、项目名称、联系人/电话、金额单位，配电箱绿色分类条，
    序号支持 Excel 原生超链接跳转直达箱体分项流水卡片，数量、单价、总价公式与底部自动求和。
    """
    ws.title = "成套设备报价(汇总)"

    headers = ["序号", "柜号", "箱柜名称", "箱柜型号", "单位", "数量", "单价", "总价", "备注"]
    col_widths = [10, 16, 26, 20, 8, 10, 16, 18, 24]
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

    comps_by_box: dict[str, list[Any]] = {}
    for comp in components:
        used = getattr(comp, "used_in", "") if hasattr(comp, "used_in") else str(comp.get("used_in", ""))
        comps_by_box.setdefault(used, []).append(comp)

    start_data_row = 8
    curr_row = start_data_row

    for i, b in enumerate(boxes, start=1):
        code = getattr(b, "code", "") if hasattr(b, "code") else str(b.get("code", "未命名"))
        name = getattr(b, "name", "") if hasattr(b, "name") else str(b.get("name", "配电箱"))
        size = getattr(b, "size", "") if hasattr(b, "size") else str(b.get("size", ""))
        loc = getattr(b, "location", "") if hasattr(b, "location") else str(b.get("location", ""))
        install = getattr(b, "install", "") if hasattr(b, "install") else str(b.get("install", ""))
        qty = getattr(b, "quantity", 1) if hasattr(b, "quantity") else (b.get("quantity", 1) or 1)
        qty_num = int(qty) if float(qty) == int(qty) else qty

        # 箱柜型号规范显示
        box_model = size if size and size != "-" else ("GGD(落地)" if "落地" in install or "柜" in name else "XM(标准)")

        b_circuits = circuits_by_box.get(code, [])
        b_comps = comps_by_box.get(code, [])
        costs = _estimate_box_costs(b, b_circuits, b_comps)

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
        c_unit_cell = ws.cell(row=curr_row, column=5, value="台")
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

        # 备注（使用部位/所属楼栋车间）
        c_loc = ws.cell(row=curr_row, column=9, value=loc or "配电间/动力间")
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


def _fill_sheets(wb, result, subtitle, template: bool):
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

        ws2 = _template_sheet(wb, "元器件汇总") or wb.create_sheet("元器件汇总")
        r = 4
        for i, c in enumerate(result.components, 1):
            q = int(c.quantity) if float(c.quantity) == int(c.quantity) else c.quantity
            r = _row(ws2, r, [i, c.name, c.spec, c.unit, q, c.used_in, c.note], template=True)

        ws3 = _template_sheet(wb, "回路明细") or wb.create_sheet("回路明细")
        r = 4
        for i, c in enumerate(result.circuits, 1):
            r = _row(ws3, r, [i, c.box, c.phase, c.breaker, c.contactor, c.ct, c.thermal,
                              c.power_kw, c.circuit_no, c.cable, c.current_a,
                              c.load_name, c.secondary_ref, c.start_method, c.note],
                     template=True, height=44, center_cols=(1, 2, 3))
        return

    # 1. 创建 Sheet 1 (成套设备报价汇总) 与 Sheet 2 (箱体分项明细卡片)
    ws_summary = wb.active
    ws_cards = wb.create_sheet("箱体分项明细")

    # 先渲染卡片明细，获取每个箱体的超链接行号
    card_anchors = _fill_box_cards_detail_sheet(
        ws_cards, result.title, subtitle,
        result.boxes, result.circuits, result.components
    )

    # 渲染第一页成套报价汇总表
    _fill_quotation_summary_sheet(
        ws_summary, result.title,
        result.boxes, result.circuits, result.components,
        card_anchors, detail_sheet_title="箱体分项明细"
    )

    # 2. Sheet 3: 箱体清单
    ws_box = wb.create_sheet("箱体清单")
    headers_box = ["序号", "设备编号", "设备名称", "防护等级", "安装方式", "安装位置", "参考尺寸", "数量(台)", "备注"]
    r_box = _setup(ws_box, result.title, subtitle, headers_box, [6, 14, 20, 10, 22, 18, 24, 10, 36])
    for i, b in enumerate(result.boxes, 1):
        r_box = _row(ws_box, r_box, [i, b.code, b.name, b.ip_rating, b.install, b.location, b.size, b.quantity, b.note])

    # 3. Sheet 4: 元器件汇总
    ws_comp = wb.create_sheet("元器件汇总")
    headers_comp = ["序号", "元器件名称", "规格型号", "单位", "数量", "用于箱体/回路", "备注"]
    r_comp = _setup(ws_comp, result.title, subtitle, headers_comp, [6, 26, 32, 8, 10, 32, 36])
    for i, c in enumerate(result.components, 1):
        q = int(c.quantity) if float(c.quantity) == int(c.quantity) else c.quantity
        r_comp = _row(ws_comp, r_comp, [i, c.name, c.spec, c.unit, q, c.used_in, c.note])

    # 4. Sheet 5: 回路明细
    ws_cir = wb.create_sheet("回路明细")
    headers_cir = ["序号", "箱体编号", "相序", "断路器", "接触器", "电流互感器", "热继电器",
                   "设备容量(kW)", "回路编号", "导线型号及敷设", "计算电流(A)",
                   "回路名称", "二次图编号", "启动方式", "备注"]
    r_cir = _setup(ws_cir, result.title, subtitle, headers_cir, [6, 12, 8, 26, 12, 14, 12, 14, 12, 34, 10, 22, 30, 14, 30])
    for i, c in enumerate(result.circuits, 1):
        r_cir = _row(ws_cir, r_cir, [i, c.box, c.phase, c.breaker, c.contactor, c.ct, c.thermal,
                                    c.power_kw, c.circuit_no, c.cable, c.current_a,
                                    c.load_name, c.secondary_ref, c.start_method, c.note],
                     height=44, center_cols=(1, 2, 3))

    # 5. Sheet 6: 技术要求与报价说明
    ws_req = wb.create_sheet("技术要求与报价说明")
    r_req = _setup(ws_req, result.title, subtitle, ["序号", "项目", "要求内容"], [6, 24, 110])
    reqs = list(result.requirements)
    observed = uncertainty_texts(result, "model") + [
        u.text for u in result.uncertainties if not u.source and u.text]
    if observed:
        reqs.append({"item": "待人工核对项", "content": "；".join(observed)})
    warnings = uncertainty_texts(result, "program")
    if warnings:
        reqs.append({"item": "程序核对告警", "content": "；".join(warnings)})
    for i, q in enumerate(reqs, 1):
        item = q.item if hasattr(q, "item") else q["item"]
        content = q.content if hasattr(q, "content") else q["content"]
        r_req = _row(ws_req, r_req, [i, item, content], height=64, center_cols=(1,))


def _fill_replacements(wb, components: list, target_brand: str = "正泰"):
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

    save_font = Font(name="微软雅黑", size=10, bold=True, color="137333")
    for i, it in enumerate(analysis["items"], 1):
        saving_text = f"↓{it['estimated_saving_pct']}%" if it['estimated_saving_pct'] > 0 else "已最优"
        curr_r = r
        r = _row(ws, r, [
            i, it["name"], it["original_spec"], it["original_brand"],
            it["recommended_model"], it["quantity"], it["unit"],
            saving_text, it["notes"]
        ], height=32, center_cols=(1, 4, 7, 8))
        if it['estimated_saving_pct'] > 0:
            ws.cell(row=curr_r, column=8).font = save_font


def _fill_changes(wb, changes):
    """变更记录：人工/语音/AI 改动逐条留痕，随清单一起交付，便于对报价做追溯。"""
    if "变更记录" in wb.sheetnames:
        del wb["变更记录"]
    ws = wb.create_sheet("变更记录")
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
    r = _setup(ws, title, subtitle, headers, [6, 38, 12, 14, 24, 14, 12, 12, 10, 16, 28])

    type_names = {
        "cabinet": "一级总柜",
        "box": "二级分箱",
        "secondary": "二次控制",
        "circuit": "出线支路",
    }
    cab_fill = PatternFill("solid", fgColor="EBF1F5")
    sec_font = Font(name="微软雅黑", size=10, italic=True, color="595959")
    bold_font = Font(name="微软雅黑", size=10, bold=True)

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


def build_workbook(result: ExtractionResult, subtitle: str, out_path: str,
                   changes=None, include_changes: bool = True,
                   template_path: str = "", target_brand: str = "正泰") -> str:
    template = bool(template_path) and os.path.exists(template_path)
    if template:
        try:
            wb = openpyxl.load_workbook(template_path)
        except Exception:  # noqa: BLE001 - 模板坏了不能拖垮导出
            template = False
            wb = _blank_workbook()
    else:
        wb = _blank_workbook()

    _fill_sheets(wb, result, subtitle, template)
    if getattr(result, "topology", None):
        _fill_topology_sheet(wb, result.topology, f"{result.title}——配电拓扑架构树", subtitle)
    if result.components:
        _fill_replacements(wb, result.components, target_brand=target_brand or "正泰")
    if include_changes and changes:
        _fill_changes(wb, changes)
    wb.save(out_path)
    return out_path


def build_project_bom_workbook(project_name: str, jobs: list[dict], out_path: str, target_brand: str = "正泰") -> str:
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

    subtitle = f"项目：{project_name} ｜ 涵盖 {len(jobs)} 份图纸、{len(all_boxes)} 台配电箱 ｜ 生成时间：{datetime.now():%Y-%m-%d %H:%M}"

    # 1. Sheet 1: 全项目成套设备报价(汇总)
    ws_summary = wb.active
    ws_cards = wb.create_sheet("箱体分项明细")

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
        all_boxes, all_circuits, all_components
    )

    # 渲染全项目成套报价汇总表（带超链接锚定卡片）
    _fill_quotation_summary_sheet(
        ws_summary, project_name,
        all_boxes, all_circuits, all_components,
        card_anchors, detail_sheet_title="箱体分项明细"
    )

    # 2. Sheet 3: 全项目采购总清单 (BOM)
    ws1 = wb.create_sheet("全项目采购总清单(BOM)")
    headers1 = ["序号", "元器件名称", "规格型号", "单位", "全项目总采购量", "各配电箱分布明细", "参考单价(元)", "预估合价(元)", "备注"]
    r1 = _setup(ws1, f"【{project_name}】电气元器件集中采购总清单(BOM)", subtitle, headers1, [6, 26, 32, 8, 14, 42, 14, 14, 24])

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
    ws_rep = wb.create_sheet(f"集中采购平替({target_brand})")
    headers_rep = ["序号", "元器件名称", "原图设计规格", "原厂品牌", f"推荐平替型号({target_brand})", "集中采购总量", "单位", "预计降本", "各配电箱分布", "对标依据与核验说明"]
    rep_sub = (
        f"全项目集中平替目标品牌：{target_brand} ｜ 涉及品种：{len(sorted_comps)} 项 ｜ "
        f"可降本采购总量：{rep_analysis['replaceable_quantity']} 件 ｜ 预计元器件总采购额直降约：{rep_analysis['estimated_overall_saving_pct']}%"
    )
    r_rep = _setup(ws_rep, f"【{project_name}】电气元器件集中采购国产化平替与降本对账表（{target_brand}）", rep_sub, headers_rep,
                   [6, 20, 26, 12, 32, 14, 8, 12, 36, 42])
    save_font = Font(name="微软雅黑", size=10, bold=True, color="137333")
    for i, it in enumerate(rep_analysis["items"], 1):
        saving_text = f"↓{it['estimated_saving_pct']}%" if it['estimated_saving_pct'] > 0 else "已最优"
        curr_r = r_rep
        r_rep = _row(ws_rep, r_rep, [
            i, it["name"], it["original_spec"], it["original_brand"],
            it["recommended_model"], it["quantity"], it["unit"],
            saving_text, it["used_in"], it["notes"]
        ], height=32, center_cols=(1, 4, 7, 8))
        if it['estimated_saving_pct'] > 0:
            ws_rep.cell(row=curr_r, column=8).font = save_font

    # Sheet 3: 配电箱成套设备台账
    ws2 = wb.create_sheet("配电箱成套设备台账")
    headers2 = ["序号", "配电箱编号", "设备名称", "防护等级", "安装方式", "安装位置", "参考尺寸(mm)", "台数", "回路总数", "备注"]
    r2 = _setup(ws2, f"【{project_name}】配电箱/配电柜成套台账", subtitle, headers2, [6, 16, 20, 12, 18, 18, 22, 10, 10, 24])
    for i, b in enumerate(all_boxes, 1):
        r2 = _row(ws2, r2, [i, b["code"], b["name"], b["ip_rating"], b["install"], b["location"], b["size"], b["quantity"], b["circuits_count"], ""], height=26, center_cols=(1, 4, 8, 9))

    # Sheet 3: 成套外壳与辅材概算
    ws3 = wb.create_sheet("成套辅材与制造估算")
    headers3 = ["序号", "配电箱编号", "回路数", "外壳估算形式/尺寸", "箱壳估算基准(元)", "铜排母线及端子辅料(元)", "装配测试工时费(元)", "单台成套制造辅价(元)", "台数", "小计(元)"]
    r3 = _setup(ws3, f"【{project_name}】配电箱成套辅料与柜体制造费测算", "基于工程经验的辅材、箱体钣金与组装试验费估算模板（单价公式可按项目调整）", headers3, [6, 16, 10, 24, 18, 20, 18, 20, 10, 16])
    for i, b in enumerate(all_boxes, 1):
        c_cnt = max(1, b["circuits_count"])
        box_base = 280.0 if "明装" in b["install"] else 320.0
        acc_base = c_cnt * 35.0
        labor_base = c_cnt * 25.0 + 80.0
        curr_row = r3
        r3 = _row(ws3, r3, [i, b["code"], c_cnt, b["size"] or f"{c_cnt}极外壳", box_base, acc_base, labor_base, f"=E{curr_row}+F{curr_row}+G{curr_row}", b["quantity"], f"=H{curr_row}*I{curr_row}"], height=26, center_cols=(1, 3, 9))

    # Sheet 4: 项目统一技术规范与说明
    ws4 = wb.create_sheet("全项目统一技术要求")
    headers4 = ["序号", "项目/分类", "要求内容及设计原则"]
    r4 = _setup(ws4, f"【{project_name}】全项目电气技术要求汇总", "汇集该项目全部图纸的设计说明、分断能力标准及特殊保护原则", headers4, [6, 26, 90])
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
            f"跨图纸层级关联：涵盖全项目 {len(all_boxes)} 台配电柜/箱及一二次回路控制原理图"
        )

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
            cell = ws.cell(row=r_idx, column=c_idx, value=val)
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

