# -*- coding: utf-8 -*-
"""前端"不编数据"契约：拆量页/首页不得写死编造的默认值、假状态与假对账。

这里锁的是会被静默改回来的硬约束（字段映射、加载态、状态判定），界面像素另由真机验收。
"""
import pathlib
import re
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent / "frontend"


class FrontendNoFakeDataTest(unittest.TestCase):
    def setUp(self):
        self.html = (ROOT / "index.html").read_text(encoding="utf-8")
        self.js = (ROOT / "app.js").read_text(encoding="utf-8")

    def test_不写死箱柜业务默认值(self):
        """尺寸/位置/安装/防护/名称这类会被当成真实数据的值，不许有兜底默认。"""
        fakes = ("800×1200×250", "挂墙明装", "配电间", "IP30",
                 "'动力配电箱'", "'标准选型'", "|| '配电箱'")
        for fake in fakes:
            self.assertNotIn(fake, self.html, f"index.html 仍有编造默认值 {fake}")
            self.assertNotIn(fake, self.js, f"app.js 仍有编造默认值 {fake}")

    def test_箱柜头部字段映射到后端真实字段(self):
        # 防护等级字段是 ip_rating；数据里没有 type 字段，柜型/尺寸都来自 size
        self.assertIn("b.raw.ip_rating", self.js)
        self.assertNotRegex(self.js, r"b\.raw\.ip\b")
        self.assertNotIn("b.raw.type", self.js)
        self.assertIn("splitBoxSize(b.raw.size)", self.js)

    def test_数据到位前是加载态而不是演示清单(self):
        self.assertIn("正在加载图纸数据…", self.html)
        self.assertIn("正在加载图纸数据…", self.js)
        self.assertIn("showLoadingState()", self.js)
        # 高亮框默认隐藏，等真实选中箱柜后再显示
        self.assertRegex(self.html, r'class="[^"]*\bhidden\b[^"]*"\s+id="extCadFocusBox"')

    def test_没有假对账与假坐标说法(self):
        for claim in ("对账已闭环", "图纸目录列出", "CAD X:", "多源并行"):
            self.assertNotIn(claim, self.js)
            self.assertNotIn(claim, self.html)
        # 对账结论只认后端 reconciliation
        self.assertIn("reconciliation", self.js)
        self.assertIn("图纸目录对账", self.js)

    def test_状态标签按真实组价结果判定(self):
        self.assertIn("function isQuoted(j)", self.js)
        self.assertIn("已识别 · 待组价", self.js)
        cell = re.search(r"function renderStatusCell\(j\)[\s\S]*?\n}", self.js)
        self.assertIsNotNone(cell)
        self.assertIn("isQuoted(j)", cell.group(0))

    def test_待核对空态不是闭环结论(self):
        self.assertIn("当前箱柜没有待核对项", self.js)

    def test_不再用浏览器原生_prompt_弹窗(self):
        for pat in ("= prompt(", "prompt('", "prompt(`"):
            self.assertNotIn(pat, self.js)
        self.assertIn("openFieldForm(", self.js)

    def test_费率是行内数字输入(self):
        for el in ('id="pricingAuxInput"', 'id="pricingLaborInput"', 'id="pricingTaxInput"'):
            self.assertIn(el, self.html)
        self.assertNotIn("pricingAuxBtn", self.html)


if __name__ == "__main__":
    unittest.main()
