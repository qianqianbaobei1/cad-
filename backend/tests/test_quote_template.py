# -*- coding: utf-8 -*-
"""成套设备组价两表模板的版面契约。

锁住客户口径：第一行就是表名（没有封面、没有 1~6 行抬头）、不写报价日期/联系人、
九列明细、四项费用结算行、总计=单台合计×数量，待核对项不单独占页签。
"""
import os
import tempfile
import unittest

import openpyxl

from extractor.excel import build_workbook, build_pricing_quotation_workbook, quote_model_from_quote_res
from extractor.quote_calc import quote_box
from extractor.schema import Box, Circuit, Component, ExtractionResult, Uncertainty


def _result() -> ExtractionResult:
    box = Box(code="02AL01", name="照明配电箱", install="挂墙明装", location="锅炉房",
              size="450*350*120", quantity=1, ip_rating="IP30")
    return ExtractionResult(
        title="测试项目",
        boxes=[box],
        circuits=[
            Circuit(box="02AL01", circuit_no="进线", breaker="SW-63A/3P", load_name="市电进线"),
            Circuit(box="02AL01", circuit_no="WL1", breaker="C9 2P C16A 6kA+ELE 30mA", load_name="照明"),
        ],
        components=[Component(name="空开", spec="C9 2P C16A 6kA+ELE 30mA", quantity=1, used_in="02AL01")],
        requirements=[],
        uncertainties=[],
    )


class QuoteTemplateTest(unittest.TestCase):
    def _export(self, result=None, **kwargs) -> openpyxl.Workbook:
        result = result or _result()
        with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as tf:
            path = tf.name
        try:
            build_workbook(result, "subtitle", path, layout="quote_template", **kwargs)
            return openpyxl.load_workbook(path)
        finally:
            if os.path.exists(path):
                os.remove(path)

    def test_组价两表在最前且没有封面(self):
        wb = self._export()
        self.assertEqual(wb.sheetnames[:2], ["组价汇总", "组价明细"])
        self.assertNotIn("封面", wb.sheetnames)

    def test_第一行就是表名_没有抬头行(self):
        wb = self._export(owner="某某建设")
        ws, wd = wb["组价汇总"], wb["组价明细"]
        self.assertEqual(ws["A1"].value, "成套设备组价汇总")
        self.assertEqual(wd["A1"].value, "成套设备组价明细")
        self.assertEqual(ws["A2"].value, "项目单位：某某建设")
        self.assertEqual(ws["A3"].value, "项目名称：测试项目")
        self.assertIn("金额单位", str(ws["H4"].value))
        # 报价日期、文件编号、报价单位、联系人电话一律不出现
        text = "\n".join(str(v) for row in ws.iter_rows(values_only=True) for v in row if v)
        for banned in ("报价日期", "文件编号", "联系人", "联系电话"):
            self.assertNotIn(banned, text)

    def test_汇总表六行表头加九列(self):
        ws = self._export()["组价汇总"]
        self.assertEqual(ws["A5"].value, "配电箱")
        headers = [ws.cell(row=6, column=c).value for c in range(1, 10)]
        self.assertEqual(headers, ["序号", "柜号", "箱柜名称", "箱柜型号", "单位", "数量", "单价", "总价", "备注"])

    def test_汇总表序号带明细表超链接(self):
        ws = self._export()["组价汇总"]
        formula = ws["A7"].value
        self.assertEqual(formula, '=HYPERLINK("#组价明细!A7:I17 ","1-1")')

    def test_明细表四项费用结算行齐全(self):
        ws = self._export()["组价明细"]
        self.assertEqual(ws["B7"].value, "柜号：02AL01")
        headers = [ws.cell(row=8, column=c).value for c in range(1, 10)]
        self.assertEqual(headers, ["序号", "元件名称", "型号规格", "单位", "数量", "单价", "总价", "生产厂家", "备注"])
        labels = [ws.cell(row=r, column=2).value for r in range(9, ws.max_row + 1)]
        for need in ("小计", "辅料", "成套制作费", "税费", "单台合计", "总计"):
            self.assertIn(need, labels)

    def test_总计等于单台合计乘数量(self):
        ws = self._export()["组价明细"]
        rows = {ws.cell(row=r, column=2).value: r for r in range(9, ws.max_row + 1)}
        unit_row, total_row = rows["单台合计"], rows["总计"]
        qty = float(ws.cell(row=total_row, column=5).value)
        self.assertAlmostEqual(float(ws.cell(row=total_row, column=7).value),
                               float(ws.cell(row=unit_row, column=7).value) * qty, places=2)

    def test_组价结果的价格直接落进模板(self):
        """组价工作台手改过的单价必须原样出现在明细表，而不是被重新估价覆盖。"""
        lines = [
            {"name": "微型断路器", "unit": "只", "qty": 3, "unit_price": 12.34},
            {"name": "壳体", "unit": "台", "qty": 1, "unit_price": 100.0},
            {"name": "铜材", "unit": "套", "qty": 1, "unit_price": 10.0},
        ]
        breakdown = quote_box(lines, 2)
        quote_res = {
            "summary": {"brand": "正泰"},
            "rows": [
                {"type": "box_header", "box_code": "AP1", "name": "动力配电箱",
                 "quantity": 2, "unit_price": breakdown["unit_total"]},
                {"type": "component", "name": "微型断路器", "design_spec": "C16/1P",
                 "replacement_model": "正泰 NXB-63 C16", "unit": "只", "quantity": 3,
                 "unit_price": 12.34, "total_price": 37.02, "brand": "正泰", "source": "手填"},
                {"type": "material", "name": "壳体", "design_spec": "500*400*200",
                 "unit": "台", "quantity": 1, "unit_price": 100.0,
                 "source": "定额", "brand": "成套定制"},
                {"type": "material", "name": "铜材", "design_spec": "母排 TMY 折算",
                 "unit": "套", "quantity": 1, "unit_price": 10.0,
                 "source": "联动", "brand": ""},
            ],
            "boxes": [{
                "box_code": "AP1", "name": "动力配电箱", "size": "500*400*200", "location": "车间",
                "quantity": 2, "breakdown": breakdown,
            }],
        }
        model = quote_model_from_quote_res(quote_res, {"project": "某项目"}, "正泰")
        box = model["boxes"][0]
        self.assertAlmostEqual(box["items"][0]["unit_price"], 12.34)
        # 壳体/铜材也是普通明细行，小计必须等于逐行相加
        self.assertEqual([i["name"] for i in box["items"]], ["微型断路器", "壳体", "铜材"])
        self.assertAlmostEqual(sum(i["qty"] * i["unit_price"] for i in box["items"]), box["subtotal"])
        self.assertAlmostEqual(box["auxiliary"], 7.35)
        self.assertAlmostEqual(box["manufacture"], breakdown["manufacture"])
        self.assertAlmostEqual(box["tax_total"], breakdown["tax"])
        # 四项费用相加必须等于单台合计，否则报价单自己就自相矛盾
        self.assertAlmostEqual(box["subtotal"] + box["auxiliary"] + box["manufacture"] + box["tax_total"],
                               box["unit_price"], places=2)
        self.assertAlmostEqual(model["total_price"], breakdown["total"], places=2)

        with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as tf:
            path = tf.name
        try:
            build_pricing_quotation_workbook({"project": "某项目"}, quote_res, path, target_brand="正泰")
            wb = openpyxl.load_workbook(path)
            self.assertEqual(wb.sheetnames, ["组价汇总", "组价明细"])
            ws = wb["组价明细"]
            self.assertAlmostEqual(float(ws["F9"].value), 12.34)
            self.assertEqual(ws["E9"].value, 3)
            self.assertAlmostEqual(float(wb["组价汇总"]["G7"].value), breakdown["unit_total"])
            summary = wb["组价汇总"]
            self.assertAlmostEqual(float(summary["H8"].value), breakdown["total"], places=2)
            self.assertEqual(summary["C9"].value, int(round(breakdown["total"])))
            self.assertEqual(summary["G9"].value, model["total_upper"])
        finally:
            if os.path.exists(path):
                os.remove(path)

    def test_待核对项不单独占页签但要在汇总里说明(self):
        result = _result()
        result.uncertainties = [Uncertainty(location="WL3", detail="备用回路无编号",
                                            severity="ERROR", resolved=False, source="program")]
        wb = self._export(result=result)
        self.assertNotIn("待核对项", wb.sheetnames)
        ws = wb["组价汇总"]
        text = "\n".join(str(v) for row in ws.iter_rows(values_only=True) for v in row if v)
        self.assertIn("待核对", text)
        self.assertIn("1 处图面存疑未确认", text)


if __name__ == "__main__":
    unittest.main()
