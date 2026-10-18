# -*- coding: utf-8 -*-
"""组价明细单表模板：屏幕上那张组价大表的 Excel 版。

组价是 AI 自动匹配出来的价格，不是供应商的正式报价单：
没有封面、没有公司中英文名、没有报价人/审核人/报价说明，也不写文件编号与项目单位。
导出只有一张「组价明细」表，行的顺序、列的语义与组价页屏幕完全一致：

    标题（项目/图纸名 + 组价明细）
    口径（平替品牌 / 辅料率 / 成套制作费率 / 税费率）
    表头（序号、柜号、名称、柜型/设计规格、报价型号规格、单位、数量、单价、总价、生产厂家、价格来源、备注）
    分类行 → 箱柜行 → 元器件明细行 → 构成行（小计/辅料/成套制作费/税费/单台合计）→ 下一柜
    合计（2 位小数）、报价总计（取整到元）+ 大写金额

金额不在本模块里算：模型里的 breakdown / 合计 / 总计全部来自 quote_calc 那条唯一计费链，
屏幕与导出因此必然同数。

模型结构::

    {
      "project": "项目名称", "brand": "正泰",
      "rates": {"aux": 0.05, "mfg": 0.09, "tax": 0.06},
      "boxes": [{"box_code": "02AL01", "breakdown": {...}, "quantity": 1, ...}],
      "rows": [{"type": "box_header"|"component"|"material", ...}],
      "total_price": 0.0, "grand_total": 0, "total_upper": "",
    }
"""
from typing import Any

from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.properties import PageSetupProperties

from .config import delivery as _delivery
from .quote_calc import default_rates, quote_box, rmb_upper

_CFG = _delivery().get("quote_template") or {}
_PALETTE = _delivery()["palette"]

FONT = _CFG.get("font", "宋体")
MONEY_FMT = "#,##0.00"
QTY_FMT = "#,##0.####"
CENTER = Alignment(horizontal="center", vertical="center")
LEFT = Alignment(horizontal="left", vertical="center")
RIGHT = Alignment(horizontal="right", vertical="center")
WRAP_LEFT = Alignment(horizontal="left", vertical="center", wrap_text=True)

_thin = Side(style="thin", color=_PALETTE.get("border_dark", "595959"))
BORDER = Border(left=_thin, right=_thin, top=_thin, bottom=_thin)

CATEGORY_FILL = PatternFill("solid", fgColor=_CFG.get("category_fill", "CCFFCC"))
BOX_FILL = PatternFill("solid", fgColor=_CFG.get("box_fill", "CCFFFF"))
HDR_FILL = PatternFill("solid", fgColor=_CFG.get("header_fill", "F2F2F2"))
TOTAL_FILL = PatternFill("solid", fgColor=_CFG.get("total_fill", "75923C"))
COST_FILL = PatternFill("solid", fgColor=_CFG.get("cost_fill", "F7F7F7"))

# 表名 / 列 / 列宽：列与列宽与组价页屏幕一致，改版面只改 config/delivery.json
SHEET = _CFG.get("detail_sheet", "组价明细")
TITLE_SUFFIX = _CFG.get("detail_title_suffix", "组价明细")
HEADERS = ["序号", "柜号", "名称", "柜型 / 设计规格", "报价型号规格", "单位",
           "数量", "单价（元）", "总价（元）", "生产厂家", "价格来源", "备注"]
LAST_COL = len(HEADERS)
WIDTHS = _CFG.get("detail_widths") or [8.0, 14.0, 26.0, 22.0, 22.0, 6.0, 8.0,
                                       14.0, 16.0, 14.0, 10.0, 16.0]

TITLE_ROW, BASIS_ROW, HEADER_ROW = 1, 2, 3
FIRST_DATA_ROW = HEADER_ROW + 1

MISSING, UNPRICED, PRICED = "missing", "unpriced", "priced"
COST_LABELS = ("小计", "辅料", "成套制作费", "税费", "单台合计")


def _num(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _pct(value: Any) -> str:
    return f"{round(_num(value) * 100, 4):g}%"


def _money(cell, value: Any):
    cell.value = value
    cell.number_format = MONEY_FMT
    cell.alignment = RIGHT
    return cell


def _merge(ws, row: int, first: int, last: int, value: Any,
           font: Font, align: Alignment, fill: PatternFill | None = None):
    if last > first:
        ws.merge_cells(start_row=row, start_column=first, end_row=row, end_column=last)
    cell = ws.cell(row=row, column=first, value=value)
    cell.font = font
    cell.alignment = align
    if fill is not None:
        cell.fill = fill
        for col in range(first, last + 1):
            ws.cell(row=row, column=col).fill = fill
    return cell


def _border_row(ws, row: int) -> None:
    for col in range(1, LAST_COL + 1):
        ws.cell(row=row, column=col).border = BORDER


def _row_state(row: dict) -> str:
    """缺价：标了缺价的价格为空；不计价：单价记 0；其余按实价写。"""
    if str(row.get("source") or "") == "缺价":
        return MISSING
    if row.get("is_unpriced") or _num(row.get("unit_price")) <= 0:
        return UNPRICED
    return PRICED


def _source_text(row: dict, state: str) -> str:
    if state == MISSING:
        return "缺价"
    if state == UNPRICED:
        return "不计价"
    return str(row.get("source") or "未标注")


def _groups(model: dict) -> list[dict]:
    """屏幕顺序：箱柜行 + 该柜明细行，按 rows 的原始顺序切组。"""
    boxes = {str(b.get("box_code") or b.get("code") or ""): b
             for b in (model.get("boxes") or [])}
    groups: list[dict] = []
    current: dict | None = None
    for row in model.get("rows") or []:
        kind = row.get("type")
        if kind == "box_header":
            current = {"header": row, "items": []}
            groups.append(current)
        elif kind in ("component", "material") and current is not None:
            current["items"].append(row)
    for group in groups:
        group["box"] = boxes.get(str(group["header"].get("box_code") or "")) or {}
    return groups


def _box_breakdown(group: dict, model: dict) -> dict:
    breakdown = group["box"].get("breakdown") or {}
    if breakdown:
        return breakdown
    quantity = group["header"].get("quantity") or 1
    return quote_box(group["items"], quantity, model.get("rates"))


def _render_header(ws, model: dict) -> None:
    project = str(model.get("project") or "").strip()
    _merge(ws, TITLE_ROW, 1, LAST_COL,
           f"{project} {TITLE_SUFFIX}" if project else TITLE_SUFFIX,
           Font(name=FONT, size=14, bold=True, color=_PALETTE.get("dark_bold_text", "1F497D")), CENTER)
    ws.row_dimensions[TITLE_ROW].height = 28

    rates = dict(default_rates())
    rates.update({k: v for k, v in (model.get("rates") or {}).items() if v is not None})
    basis = (f"平替品牌：{model.get('brand') or '按图'} ｜ 辅料率 {_pct(rates['aux'])} ｜ "
             f"成套制作费率 {_pct(rates['mfg'])} ｜ 税费率 {_pct(rates['tax'])} ｜ 金额单位：人民币元")
    _merge(ws, BASIS_ROW, 1, LAST_COL, basis,
           Font(name=FONT, size=9, color=_PALETTE.get("sub_text", "404040")), CENTER)
    ws.row_dimensions[BASIS_ROW].height = 18

    for j, head in enumerate(HEADERS, start=1):
        cell = ws.cell(row=HEADER_ROW, column=j, value=head)
        cell.font = Font(name=FONT, size=10, bold=True, color=_PALETTE.get("meta_text", "000000"))
        cell.fill = HDR_FILL
        cell.alignment = CENTER
        cell.border = BORDER
    ws.row_dimensions[HEADER_ROW].height = 22


def _render_item(ws, row: int, item: dict) -> None:
    state = _row_state(item)
    font = Font(name=FONT, size=10)
    ws.cell(row=row, column=1, value=item.get("seq") or "-").alignment = CENTER
    ws.cell(row=row, column=2, value="").alignment = CENTER
    ws.cell(row=row, column=3, value=item.get("name") or "").alignment = LEFT
    ws.cell(row=row, column=4, value=item.get("design_spec") or "—").alignment = LEFT
    ws.cell(row=row, column=5, value=item.get("replacement_model") or
            ("待指定报价型号" if state == MISSING else "")).alignment = LEFT
    ws.cell(row=row, column=6, value=item.get("unit") or "只").alignment = CENTER
    qty = ws.cell(row=row, column=7, value=item.get("quantity") or 1)
    qty.number_format = QTY_FMT
    qty.alignment = RIGHT

    if state == MISSING:  # 缺价行单价与总价留空，只标来源
        _money(ws.cell(row=row, column=8), None)
        _money(ws.cell(row=row, column=9), None)
    elif state == UNPRICED:
        _money(ws.cell(row=row, column=8), 0.0)
        _money(ws.cell(row=row, column=9), 0.0)
    else:
        _money(ws.cell(row=row, column=8), _num(item.get("unit_price")))
        _money(ws.cell(row=row, column=9), _num(item.get("total_price")))

    ws.cell(row=row, column=10, value=item.get("brand") or "—").alignment = LEFT
    ws.cell(row=row, column=11, value=_source_text(item, state)).alignment = CENTER
    note = "甲供" if state == UNPRICED else ""
    if not note and item.get("type") == "component":
        note = str(item.get("location") or "")
    ws.cell(row=row, column=12, value=note).alignment = LEFT

    for col in range(1, LAST_COL + 1):
        ws.cell(row=row, column=col).font = font
    _border_row(ws, row)
    ws.row_dimensions[row].height = 18


def _render_box(ws, row: int, seq: int, group: dict, model: dict) -> int:
    header = group["header"]
    breakdown = _box_breakdown(group, model)
    comp_count = len([i for i in group["items"] if i.get("type") == "component"])

    ws.cell(row=row, column=1, value=f"1-{seq}").alignment = CENTER
    ws.cell(row=row, column=2, value=header.get("box_code") or "").alignment = LEFT
    ws.cell(row=row, column=3, value=f"{header.get('name') or '配电箱'}（{comp_count} 项）").alignment = LEFT
    ws.cell(row=row, column=4, value=header.get("design_spec") or "—").alignment = LEFT
    ws.cell(row=row, column=5, value="").alignment = LEFT
    ws.cell(row=row, column=6, value=header.get("unit") or "台").alignment = CENTER
    qty = ws.cell(row=row, column=7, value=header.get("quantity") or 1)
    qty.number_format = QTY_FMT
    qty.alignment = RIGHT
    _money(ws.cell(row=row, column=8), _num(breakdown.get("unit_total", header.get("unit_price"))))
    _money(ws.cell(row=row, column=9), _num(breakdown.get("total", header.get("total_price"))))
    ws.cell(row=row, column=10, value="").alignment = LEFT
    ws.cell(row=row, column=11, value="").alignment = CENTER
    ws.cell(row=row, column=12, value=header.get("location") or "—").alignment = LEFT
    for col in range(1, LAST_COL + 1):
        cell = ws.cell(row=row, column=col)
        cell.fill = BOX_FILL
        cell.font = Font(name=FONT, size=10, bold=True)
        cell.border = BORDER
    ws.row_dimensions[row].height = 20
    row += 1

    for item in group["items"]:
        _render_item(ws, row, item)
        row += 1

    rates = dict(default_rates())
    rates.update({k: v for k, v in (breakdown.get("rates") or model.get("rates") or {}).items()
                   if v is not None})
    costs = [
        ("小计", breakdown.get("subtotal"), ""),
        (f"辅料（小计 × {_pct(rates['aux'])}）", breakdown.get("auxiliary"), "费率"),
        (f"成套制作费（(小计+辅料) × {_pct(rates['mfg'])}）", breakdown.get("manufacture"), "费率"),
        (f"税费（前三项 × {_pct(rates['tax'])}）", breakdown.get("tax"), "费率"),
        ("单台合计", breakdown.get("unit_total"), ""),
    ]
    for label, value, source in costs:
        bold = label == "单台合计"
        font = Font(name=FONT, size=10, bold=bold)
        ws.cell(row=row, column=1, value="-").alignment = CENTER
        _merge(ws, row, 2, 8, label, font, LEFT)
        # 构成行的金额落在「总价」列，与屏幕一致
        _money(ws.cell(row=row, column=9), _num(value))
        ws.cell(row=row, column=10, value="").alignment = LEFT
        ws.cell(row=row, column=11, value=source).alignment = CENTER
        ws.cell(row=row, column=12, value="").alignment = LEFT
        for col in range(1, LAST_COL + 1):
            ws.cell(row=row, column=col).font = font
            ws.cell(row=row, column=col).border = BORDER
            if bold:
                ws.cell(row=row, column=col).fill = COST_FILL
        ws.row_dimensions[row].height = 17
        row += 1

    return row


def _render_totals(ws, row: int, model: dict) -> int:
    total_price = _num(model.get("total_price"))
    grand_total = model.get("grand_total")
    grand_total = int(grand_total) if grand_total is not None else int(round(total_price))
    upper = model.get("total_upper") or rmb_upper(grand_total)
    units = sum(int(_num(g["header"].get("quantity")) or 1) for g in _groups(model))

    # 底部合计/报价总计：深底白字，与屏幕底部冻结行同样醒目
    bold = Font(name=FONT, size=10, bold=True, color=_PALETTE.get("header_text", "FFFFFF"))
    _merge(ws, row, 1, 2, "合计", bold, CENTER, TOTAL_FILL)
    _merge(ws, row, 3, 8, f"共 {len(_groups(model))} 个箱柜 / {units} 台", bold, LEFT, TOTAL_FILL)
    _money(ws.cell(row=row, column=9), round(total_price, 2))
    for col in range(1, LAST_COL + 1):
        ws.cell(row=row, column=col).font = bold
        ws.cell(row=row, column=col).fill = TOTAL_FILL
        ws.cell(row=row, column=col).border = BORDER
    ws.row_dimensions[row].height = 20
    row += 1

    _merge(ws, row, 1, 2, "报价总计", bold, CENTER, TOTAL_FILL)
    _merge(ws, row, 3, 8, f"大写：{upper}", bold, LEFT, TOTAL_FILL)
    cell = ws.cell(row=row, column=9, value=grand_total)
    cell.number_format = "#,##0"
    cell.alignment = RIGHT
    for col in range(1, LAST_COL + 1):
        ws.cell(row=row, column=col).font = bold
        ws.cell(row=row, column=col).fill = TOTAL_FILL
        ws.cell(row=row, column=col).border = BORDER
    ws.row_dimensions[row].height = 22
    return row + 1


def _render_category(ws, row: int, model: dict) -> int:
    groups = _groups(model)
    units = sum(int(_num(g["header"].get("quantity")) or 1) for g in groups)
    font = Font(name=FONT, size=10, bold=True)
    _merge(ws, row, 1, 2, "一", font, CENTER, CATEGORY_FILL)
    _merge(ws, row, 3, 8, f"配电箱（{len(groups)} 个箱柜 / {units} 台）", font,
           LEFT, CATEGORY_FILL)
    _money(ws.cell(row=row, column=9), _num(model.get("total_price")))
    _merge(ws, row, 10, LAST_COL, "", font, LEFT, CATEGORY_FILL)
    for col in range(1, LAST_COL + 1):
        ws.cell(row=row, column=col).font = font
        ws.cell(row=row, column=col).fill = CATEGORY_FILL
        ws.cell(row=row, column=col).border = BORDER
    ws.row_dimensions[row].height = 20
    return row + 1


def fill(wb, model: dict) -> Any:
    """把组价模型渲染进工作簿：唯一一张「组价明细」表，冻结表头行。"""
    ws = wb.active
    ws.title = SHEET
    for j, width in enumerate(WIDTHS, start=1):
        ws.column_dimensions[get_column_letter(j)].width = width

    _render_header(ws, model)
    row = FIRST_DATA_ROW
    row = _render_category(ws, row, model)
    for seq, group in enumerate(_groups(model), start=1):
        row = _render_box(ws, row, seq, group, model)
    row = _render_totals(ws, row, model)

    ws.freeze_panes = f"A{FIRST_DATA_ROW}"
    ws.print_area = f"A1:{get_column_letter(LAST_COL)}{row - 1}"
    ws.sheet_properties.pageSetUpPr = PageSetupProperties(fitToPage=True)
    ws.page_setup.orientation = "landscape"
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 0
    return wb
